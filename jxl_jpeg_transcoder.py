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
import json
import re
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time
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
    # NOT "nothing was deleted": the delete gate runs after the pool drains,
    # so sources whose output was already written and verified BEFORE this
    # latched are still removed. That is safe — each one passed every gate —
    # but the old wording contradicted the very next line of the log.
    logger.error("  Queued files were NOT attempted. Sources already converted and "
                 "verified in this run may still be deleted below; nothing that was "
                 "not attempted is touched.")
    logger.error("  Free space, then re-run: sync mode resumes where this stopped.")


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


def _promote_from_staging(write_path, final_path) -> bool:
    """Move one finished output out of staging. True when it landed.

    A cross-volume move is copy-then-unlink, so an ENOSPC part way through
    leaves a TRUNCATED file at the destination — with a fresh mtime. That is
    the worst possible outcome: smart-sync compares timestamps, sees something
    newer than the source, and skips the reconversion forever. The good copy is
    still in staging, so removing whatever landed loses nothing and puts the
    destination back to a state a later run will fix.

    A destination volume that is simply FULL also has to stop the run rather
    than produce one MOVE FAILED line per remaining file, which is what the
    disk-full abort exists for.
    """
    pre_existed = final_path.exists()
    try:
        size = write_path.stat().st_size
    except OSError:
        size = 0
    try:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(write_path), str(final_path))
        return True
    except OSError as e:
        logger.error(f"  MOVE FAILED, kept in staging | {write_path.name} -> "
                     f"{final_path} | {e}")
        # Only when the staging copy survived: if it is gone the move actually
        # completed and something else raised.
        if write_path.exists() and final_path.exists():
            try:
                final_path.unlink()
                logger.error(
                    f"    Removed the partial file left at the destination"
                    + (" (it had OVERWRITTEN an existing output, which was already "
                       "corrupt by then)" if pre_existed else "")
                    + " — the complete copy is still in staging.")
            except OSError as e2:
                logger.error(f"    Could NOT remove the partial destination file "
                             f"({e2}). Delete {final_path} by hand before re-running: "
                             f"a later sync run would treat it as up to date.")
        _abort_if_disk_full(final_path.parent, size)
        return False


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

PROVENANCE_CHECK = "path"
# [with DELETE_SOURCE, modes 2/4/5/6/7] How an EXISTING output is matched to
# the source about to replace it. Those modes drop folder structure, so two
# sources in different folders resolve to the same output; without this a
# second run overwrote the first archive and deleted both originals.
# "path" (default) compares the recorded LOCATION; "content" also accepts
# matching source bytes, so it survives MOVED folders. See _provenance_ok.
#
# NOTE: the JXL -> JPEG LOSSLESS path writes no marker. Its output has to
# stay byte-identical to the original JPEG, and injecting XMP would break
# exactly the promise that path exists to keep. It uses checksums.md5
# instead: the stored hash IS the original JPEG, so an existing output that
# matches it came from this JXL.

DELETE_SKIPPED = False
# [DELETE_SOURCE only] Also delete sources whose output ALREADY EXISTS — the
# files this run reports as SKIP.
#
# Without it an archive interrupted between the conversion and the unlink can
# never be finished: the leftover is skipped on every later run, and a skip
# blocks the delete, so it stays until everything is redone with --overwrite.
#
# A SKIP proves far less than a conversion. A conversion means "this run wrote
# the file AND it passed the integrity check". A skip means only "a file with
# that NAME exists and its mtime is not older than the source", and mtime is not
# content: it reads "newer" after a copy, a backup restore or a touch, and says
# nothing about whether that output came from THIS file.
#
# What stands in its place depends on the direction, and the difference is large
# enough that the two are not the same feature:
#
#   * LOSSLESS transcode (JPEG <-> JXL) — checksums.md5 holds the SOURCE's md5
#     keyed by the OUTPUT's name, so provenance can be PROVEN: hash the source
#     and compare. That is stronger than any pixel comparison and cheaper (no
#     decode). Applied whenever a checksum exists.
#   * LOSSY convert (JXL -> JPEG/PNG, or PNG/JPEG -> JXL lossy) — nothing is
#     stored and nothing can be re-derived, so the gate is the STRUCTURAL check
#     alone. There is no way to prove that output came from that source. The run
#     warns, and the wrapper asks for an extra confirmation.

DELETE_SOURCE = False
# Delete source after successful encode/decode, in ANY mode.
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

_MIN_EFFECTIVE_DISTANCE = 0.05
# cjxl clamps every lossy distance at or below this to the same value:
# --distance 0.005 and 0.05 were measured producing BYTE-IDENTICAL output.
# Same constant, and the same warning, as jxl_tiff_encoder.py.


def _warn_distance_clamp(distance) -> None:
    """Say so when a requested distance buys nothing.

    Call AFTER setup_logger(): on the module-level logger a warning falls
    through to logging.lastResort — unformatted on stderr, never in the log
    file (bug #238).
    """
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return
    if 0 < d < _MIN_EFFECTIVE_DISTANCE:
        logger.warning(
            f"--distance {d} behaves exactly like {_MIN_EFFECTIVE_DISTANCE}: cjxl "
            f"clamps every lossy distance below that to the same output. "
            f"Use --distance 0 for true lossless.")


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
        logger.error("  Hint: marker-anchored modes (6/7) drop ONE folder level under the marker, "
                     "so nested marker folders (X/X/photo.tif and X/photo.tif) and same-named files "
                     "in sibling recipe folders both collapse onto the same .jxl name.")
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


SRC_PREFIX = "jxlphoto-src:"
SRCSUM_PREFIX = "jxlphoto-srcsum:"
# Must match the encoder's SRC_XMP_PREFIX / SRCSUM_XMP_PREFIX: they record
# WHICH source made an output, so a later run can refuse to overwrite one
# archive with a different file that happens to share its name.

# --- Provenance: which source made this output -----------------------------
# Duplicated across the backend scripts on purpose (see AGENTS.md); enforced by
# tests/test_helper_parity.py. Fix bugs in ALL copies.
#
# The modes that COLLAPSE folder structure (2/4/5/6/7) drop the source's folder
# from the output path, so two sources in different folders resolve to the same
# output. _abort_on_duplicate_outputs catches that inside one run; only a
# recorded marker catches it ACROSS runs — and with --delete-source the second
# run would otherwise overwrite the first archive and delete both originals.
_COLLAPSING_MODES = frozenset({2, 4, 5, 6, 7})


def _run_collapses_structure(mode, output_arg, source_root) -> bool:
    """Does THIS run drop the source's folder from the output path?

    _COLLAPSING_MODES holds the modes that always do. Mode 0 also does, but only
    when an output folder was given: every file then lands in that one folder,
    flat, exactly like mode 2 — so a second run over a DIFFERENT source folder
    writes the same names into it, and with --delete-source it overwrites the
    first archive after that archive's own source is already gone. Mode 0 is
    flat, so _abort_on_duplicate_outputs never sees this: the recorded marker is
    the only defence there is.

    Mode 0 IN PLACE (no output folder) is NOT collapsing and must not be treated
    as one: demanding a marker there would refuse existing archives that can
    never collide, which is the dead end #271 exists to avoid.
    """
    if mode in _COLLAPSING_MODES:
        return True
    if mode != 0 or not output_arg:
        return False
    try:
        return (os.path.normcase(os.path.abspath(str(output_arg)))
                != os.path.normcase(os.path.abspath(str(source_root))))
    except (OSError, ValueError):
        return True     # cannot tell -> assume it collapses (fail closed)


def _source_path_id(src_path) -> str:
    """Stable id for a source's LOCATION. Free to compute."""
    norm = os.path.normcase(os.path.abspath(str(src_path)))
    return hashlib.sha256(norm.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _file_content_id(paths) -> str:
    """Stable id for a source's BYTES (one path, or a group of them in order).

    Hashing the file rather than the decoded image on purpose: recomputing this
    at check time must not cost a full decode. Read in 1 MB blocks so a 700 MB
    source is never held in memory.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    outer = hashlib.sha256()
    for p in paths:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        outer.update(h.digest())
    return outer.hexdigest()[:16]


def _read_source_markers_batch(outputs: list) -> dict:
    """{output path: {'src': id|None, 'srcsum': id|None}} in as few exiftool
    calls as possible — one per file would be minutes on a large library.

    A file whose markers cannot be read comes back with both None, which the
    caller treats as "cannot prove anything": fail closed.
    """
    markers = {str(o): {"src": None, "srcsum": None} for o in outputs}
    if not outputs:
        return markers
    batch_lines = ["-j", "-s", "-s", "-XMP-dc:Relation",
                   "-charset", "FileName=UTF8", "-charset", "UTF8"]
    BATCH = 400
    for i in range(0, len(outputs), BATCH):
        chunk = outputs[i:i + BATCH]
        argfile = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             dir=TEMP_DIR, encoding="utf-8",
                                             newline=chr(10)) as af:
                af.write(chr(10).join(batch_lines + [str(o) for o in chunk]))
                af.write(chr(10))
                argfile = af.name
            r = subprocess.run([_get_exiftool_cmd(), "-@", argfile],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180)
            if not r.stdout:
                logger.warning(f"Provenance: could not read markers for a batch of "
                               f"{len(chunk)} file(s) (rc={r.returncode})")
                continue
            # exiftool exits non-zero when it fails on ANY file of the batch but
            # still prints valid JSON for the rest — use what came back.
            data = json.loads(r.stdout)
            for entry in data:
                src = entry.get("SourceFile")
                rel = entry.get("Relation")
                if src is None or rel is None:
                    continue
                values = rel if isinstance(rel, list) else [str(rel)]
                info = {"src": None, "srcsum": None}
                for token in values:
                    token = str(token).strip()
                    if token.startswith(SRC_PREFIX):
                        info["src"] = token[len(SRC_PREFIX):]
                    elif token.startswith(SRCSUM_PREFIX):
                        info["srcsum"] = token[len(SRCSUM_PREFIX):]
                key = str(Path(src))
                if key in markers:
                    markers[key] = info
        except Exception as e:
            logger.warning(f"Provenance: marker batch failed ({e}); "
                           f"{len(chunk)} file(s) cannot be verified")
        finally:
            if argfile:
                try:
                    os.unlink(argfile)
                except OSError:
                    pass
    return markers


def _provenance_ok(info: dict, src_paths, mode_check: str) -> bool:
    """Did `src_paths` make the output these markers came from?

    `path` compares the recorded LOCATION: free, and it survives re-converting a
    file in place. `content` also accepts a matching set of source BYTES, so it
    survives a MOVED folder — deliberately a superset, because content alone
    would refuse a legitimately re-exported file and break the sync workflow.
    """
    first = src_paths[0] if isinstance(src_paths, (list, tuple)) else src_paths
    if info.get("src") and info["src"] == _source_path_id(first):
        return True
    if mode_check == "content" and info.get("srcsum"):
        try:
            return info["srcsum"] == _file_content_id(src_paths)
        except OSError:
            return False
    return False


def _provenance_marker_args(src_paths):
    """exiftool argfile lines recording WHICH source made an output."""
    first = src_paths[0] if isinstance(src_paths, (list, tuple)) else src_paths
    lines = ["-XMP-dc:Relation+=" + SRC_PREFIX + _source_path_id(first)]
    try:
        lines.append("-XMP-dc:Relation+=" + SRCSUM_PREFIX + _file_content_id(src_paths))
    except OSError:
        pass        # never fail a conversion over a marker
    return lines


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
# What the deletion actually did. Module-level because the delete gate lives in
# process_group while main() owns the summary — without this the most
# destructive thing the tool does never reached emit_summary_json.
_delete_stats = {"deleted": 0, "deleted_archived": 0, "kept": 0}

# Machine-readable run summary for the jxl_photo.py wrapper.
#
# A manifest run spawns one child PER ENTRY, each writing its own log file, so
# the wrapper had no way to total a multi-entry run: the user saw only the last
# entry's "Done:" line and had to open N logs to find out whether anything
# failed. The wrapper consumes this line and does NOT print it.
#
# Unlike the encoder/decoder, this script's counters live inside the cmd_*
# functions, which only return (errors, cancelled). Rather than change three
# return signatures, each cmd_* records its totals here and main() emits once.
SUMMARY_PREFIX = "##JXLSUM## "
SUMMARY_MAX_FAILURES = 200

_run_summary = {"ok": 0, "overwritten": 0, "skipped": 0, "errors": 0,
                "dry_run": False, "extras": {}, "failures": [], "log": ""}


def record_summary(*, ok=0, overwritten=0, skipped=0, errors=0, log_file="",
                   extras=None, failures=None, dry_run=False):
    """Store this command's totals for main() to emit. Overwrites, not adds:
    exactly one cmd_* runs per invocation."""
    _run_summary.update({
        "ok": ok, "overwritten": overwritten, "skipped": skipped,
        "errors": errors, "dry_run": dry_run,
        "extras": {k: v for k, v in (extras or {}).items() if v},
        "failures": list(failures or []),
        "log": str(log_file),
    })


def emit_summary_json(enabled):
    """Print one JSON line the wrapper can aggregate. No-op without the flag."""
    if not enabled:
        return
    failures = _run_summary["failures"]
    payload = dict(_run_summary)
    payload["script"] = Path(__file__).stem
    payload["failures"] = [{"file": f, "reason": r} for f, r in failures[:SUMMARY_MAX_FAILURES]]
    payload["failures_truncated"] = len(failures) > SUMMARY_MAX_FAILURES
    try:
        # Straight to stdout, not through the logger: the timestamp prefix and
        # the log file copy would both be noise.
        print(SUMMARY_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception:
        pass  # a summary line must never take down a finished run


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
    _log_delete_summary()
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
            # Do NOT rewrite the file with only the parsed boxes — that would
            # silently drop the trailing bytes and turn a file that fails the
            # integrity gate into one that passes it (same rule as the extended
            # box branch below, and as the encoder).
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

# Matched case-insensitively against f.suffix.lower(): globbing each spelling
# ("*.jpg", "*.JPG", ...) missed mixed case like "Photo.Jpg" on case-sensitive
# filesystems (Linux/macOS) — the file was skipped in silence.
JPEG_EXTS = frozenset({".jpg", ".jpeg", ".jfif", ".jpe"})
JXL_EXTS = frozenset({".jxl"})
PNG_EXTS = frozenset({".png"})


def _iter_by_ext(paths, exts, root=None, skip_tool_output=False):
    seen, files, skipped = set(), [], 0
    _scan = _scan_state(root)
    for f in paths:
        _scan_tick(_scan, len(files))
        if f.suffix.lower() not in exts:
            continue
        try:
            if not f.is_file():
                continue
        except OSError:
            continue
        if skip_tool_output and root is not None and _is_tool_output_path(f, root):
            skipped += 1
            continue
        try:
            key = f.resolve()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            files.append(f)
    _scan_done(_scan, len(files))
    return sorted(files), skipped


def find_jpegs_flat(input_path: Path):
    files, _ = _iter_by_ext(input_path.glob("*"), JPEG_EXTS)
    return files

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
    files, skipped = _iter_by_ext(input_path.rglob("*"), JPEG_EXTS,
                                  root=input_path, skip_tool_output=True)
    if skipped:
        logger.info(f"Skipped {skipped} JPEG file(s) inside toolkit output folders "
                    f"({', '.join(sorted(_TOOL_DECODE_OUTPUT_FOLDERS))})")
    return files

def find_jxls_flat(input_path: Path):
    files, _ = _iter_by_ext(input_path.glob("*"), JXL_EXTS)
    return files

def find_jxls_recursive(input_path: Path):
    # Unfiltered on purpose: encoder/transcoder-produced JXL folders are
    # legitimate decode sources (the round-trip depends on finding them).
    files, _ = _iter_by_ext(input_path.rglob("*"), JXL_EXTS)
    return files

def find_pngs_recursive(input_path: Path):
    files, skipped = _iter_by_ext(input_path.rglob("*"), PNG_EXTS,
                                  root=input_path, skip_tool_output=True)
    if skipped:
        logger.info(f"Skipped {skipped} PNG file(s) inside toolkit output folders")
    return files

def find_pngs_flat(input_path: Path):
    files, _ = _iter_by_ext(input_path.glob("*"), PNG_EXTS)
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
        # Check if source is newer than destination. A file vanishing between
        # the exists() above and these stats (TOCTOU) must not raise: this runs
        # BEFORE the worker's own try block, so the exception escaped as a bare
        # "error" with no useful message. Treat it as stale and attempt the
        # conversion, exactly like the TIFF encoder/decoder do.
        try:
            return src.stat().st_mtime > dst.stat().st_mtime
        except OSError:
            return True
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
    # The pool submits every task up front, so a run that has given up cannot
    # stop scheduling — it stops HERE instead. Queued files return untouched
    # and are reported as "not attempted", never as errors.
    _why = _aborted()
    if _why:
        return (str(src_path), "aborted", _why, None)

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

        # WHICH source made this output. Written BEFORE reorder_jxl_boxes:
        # every exiftool edit re-appends its boxes after the codestream, so
        # doing it afterwards would undo the reorder (bug #134's class).
        try:
            _run_exiftool_argfile(
                ["-overwrite_original"] + _provenance_marker_args(src_path)
                + [str(write_path)], timeout=60)
        except Exception as _e_prov:
            logger.debug(f"Provenance marker skipped: {_e_prov}")

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
        # Was the disk the real cause? The codec exits 0 while writing a
        # truncated file when the volume is full, so this arrives as an
        # integrity failure with nothing pointing at the drive.
        try:
            _need = src_path.stat().st_size
        except OSError:
            _need = 0
        _abort_if_disk_full(write_path.parent, _need)
        return (str(src_path), "error", str(e), None)

def decode_one_transcode(jxl_path: Path, write_path: Path, final_path: Path,
                         verify: bool, reconvert_val: bool, smart: bool) -> tuple:
    # The pool submits every task up front, so a run that has given up cannot
    # stop scheduling — it stops HERE instead. Queued files return untouched
    # and are reported as "not attempted", never as errors.
    _why = _aborted()
    if _why:
        return (str(jxl_path), "aborted", _why, None)

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
        # Was the disk the real cause? The codec exits 0 while writing a
        # truncated file when the volume is full, so this arrives as an
        # integrity failure with nothing pointing at the drive.
        try:
            _need = jxl_path.stat().st_size
        except OSError:
            _need = 0
        _abort_if_disk_full(write_path.parent, _need)
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
    """Transcode every pair in parallel.

    Called ONCE with every planned pair. It used to be called once per output
    FOLDER, which throttled the run: a folder with fewer files than `workers`
    could never fill the pool and every folder boundary drained it. The staging
    move stays per-folder and still runs in bulk — it fires when that folder's
    last file lands, so staging holds only the folders still in flight.
    """
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None
    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    ext = ".jpg" if decode else ".jxl"
    tasks = []
    for src, final_out in group_pairs:
        write_out = (staging_dir / f"{uuid.uuid4().hex}_{src.stem}{ext}") if use_staging else final_out
        tasks.append((src, write_out, final_out))

    moved_finals = set()

    def _move_dest_from_staging(dest_tasks, status_map):
        """Bulk-move one destination folder's outputs out of staging."""
        moved = 0
        for src, write_out, final_out in dest_tasks:
            status = status_map.get(str(src), "error")
            if status not in ("ok", "reconvert"):
                # "aborted" is as silent as "skipped": nothing was written,
                # and one line per never-attempted file is the wall of noise
                # the abort exists to prevent.
                if status not in ("skipped", "aborted"):
                    if write_out.exists():
                        logger.warning(f"  KEEP in staging ({status}) | {write_out.name}")
                    # else: the worker already deleted the bad output itself
                    # (e.g. md5_fail) — logging KEEP for a file that no longer
                    # exists sends the user hunting for nothing.
                continue
            if not write_out.exists():
                _delete_stats["kept"] += 1
                logger.warning(f"  KEEP (staging file missing) | {write_out.name}")
                continue
            # A locked/readonly destination must not abort the whole batch:
            # the file stays in staging and is logged for manual recovery.
            if _promote_from_staging(write_out, final_out):
                moved += 1
                moved_finals.add(os.path.normcase(str(final_out)))
        if moved:
            logger.info(f" -> Moved {moved} file(s) from staging to {dest_tasks[0][2].parent}")

    # Per-destination bookkeeping so a folder can be flushed the moment its last
    # file lands, instead of stalling the pool at every folder boundary.
    tasks_by_dest = {}
    pending_by_dest = {}
    for task in tasks:
        dest = task[2].parent
        tasks_by_dest.setdefault(dest, []).append(task)
        pending_by_dest[dest] = pending_by_dest.get(dest, 0) + 1

    results = []
    status_map = {}
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
                result = fut.result()
            except Exception as e:
                # An exception escaped the worker entirely — one bad file must
                # not kill the whole batch.
                n, total = next_count()
                logger.error(f"[{n}/{total}] ERROR | {task[0].name} | {e}")
                result = (str(task[0]), "error", str(e), None)
            results.append(result)
            status_map[result[0]] = result[1]

            # Flush this destination folder once every one of its files is done.
            # Runs on the main thread (inside the as_completed loop), so no two
            # moves ever race for the same file.
            dest = task[2].parent
            pending_by_dest[dest] -= 1
            if use_staging and pending_by_dest[dest] == 0:
                _move_dest_from_staging(tasks_by_dest[dest], status_map)

    if use_staging:
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

    # Any mode, not just 8: the mode decides where the output lands, deleting
    # the source is a separate opt-in. Every gate below certifies THIS run's
    # output at its FINAL path, which is mode-independent.
    if DELETE_SOURCE:
        deleted = 0
        deleted_skipped = 0
        src_map = {str(s): (s, f) for s, _, f in tasks}
        for result in results:
            status = result[1]
            src_md5 = result[3] if len(result) > 3 else None
            # A skip is admitted only with DELETE_SKIPPED, and only so the
            # checks below can judge it on the FILE — never on the timestamp
            # that produced the skip.
            was_skipped = status == "skipped"
            if status not in ("ok", "reconvert") and not (DELETE_SKIPPED and was_skipped):
                continue
            src_path, final_file = src_map.get(result[0], (None, None))
            if src_path is None or not final_file.exists():
                continue
            # A staged output whose move FAILED leaves a stale pre-existing
            # file at the final path: it passes exists()+integrity below, but
            # it is not the file this run wrote and verified. Fail closed.
            #
            # A SKIPPED file is the one legitimate exception: nothing was
            # written, so nothing was staged, so it can never be in
            # moved_finals. Without this the flag would look like it worked and
            # silently delete nothing whenever staging is configured.
            if (use_staging and not was_skipped
                    and os.path.normcase(str(final_file)) not in moved_finals):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (output never left staging) | {src_path.name}")
                continue
            if was_skipped:
                # This run wrote nothing, so there is no src_md5 from it — but
                # checksums.md5 can PROVE provenance, which is stronger than
                # anything the encoder's pixel comparison offers: it holds the
                # SOURCE's md5 keyed by the OUTPUT's name.
                #   encode (JPEG -> JXL): hash the source JPEG, compare.
                #   decode (JXL -> JPEG): the stored md5 IS the original JPEG's,
                #     so compare it against the recovered JPEG on disk.
                stored = read_md5_db(final_file if not decode else src_path)
                if stored is None:
                    if DELETE_SOURCE_REQUIRE_MD5:
                        _delete_stats["kept"] += 1
                        logger.warning(
                            f" KEEP (already archived, but no checksum to prove it came "
                            f"from this file) | {src_path.name}")
                        continue
                    logger.warning(
                        f" DELETING an already-archived source with NO provenance check "
                        f"(no checksum stored) | {src_path.name}")
                else:
                    actual = md5_of_file(src_path if not decode else final_file)
                    if actual != stored:
                        _delete_stats["kept"] += 1
                        logger.warning(
                            f" KEEP (already-archived output does NOT match this source: "
                            f"checksum mismatch) | {src_path.name}")
                        continue
                    logger.debug(f" provenance OK (MD5) | {src_path.name}")
            elif STORE_MD5 and DELETE_SOURCE_REQUIRE_MD5 and not decode:
                if src_md5 is None or read_md5_db(final_file) is None:
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (MD5 not confirmed) | {src_path.name}")
                    continue
            if not decode:
                # Encode direction: structural validity is not enough — the
                # JXL must also carry jbrd, otherwise the original JPEG can
                # never be recovered bit-exactly (README's promise). This
                # check is INDEPENDENT of MD5 storage (--no-md5 must not
                # weaken it).
                if final_file.suffix.lower() == '.jxl' and not has_jbrd_box(final_file):
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (output has no jbrd box; JPEG not recoverable) | {src_path.name}")
                    continue
            if decode and not was_skipped and not _tool_at_least("djxl", 0, 12):
                # djxl < 0.12 has no --reconstruct_jpeg, so bit-exact recovery
                # is NOT guaranteed by the tool. The source may only be
                # deleted when the MD5 comparison ran AND passed THIS run
                # (result[3]). --no-verify skips that comparison entirely —
                # without it, deletion would rest on the structural SOI/EOI
                # check alone: too weak for an irreversible gate.
                #
                # Not applied to a SKIP: no djxl ran at all there, and the
                # provenance block above already compared the JPEG on disk
                # against the original's stored md5 — which is the very
                # bit-exactness this check is a proxy for.
                if not result[3]:
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (djxl<0.12 and recovery not MD5-verified) | {src_path.name}")
                    continue
            if not _verify_file_integrity(final_file):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (output failed integrity check) | {src_path.name}")
                continue
            try:
                src_path.unlink()
                deleted += 1
                _delete_stats["deleted"] += 1
                if was_skipped:
                    _delete_stats["deleted_archived"] += 1
                if was_skipped:
                    deleted_skipped += 1
                    logger.info(f" DELETED source (already archived) | {src_path.name}")
                else:
                    logger.info(f" DELETED source | {src_path.name}")
            except OSError as e:
                # PermissionError is common on Windows (AV, Explorer preview,
                # open viewer) — warn and continue instead of killing the batch.
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (could not delete source) | {src_path.name}: {e}")
        if deleted:
            _sk = f" ({deleted_skipped} already archived)" if deleted_skipped else ""
            logger.info(f" -> Deleted {deleted} source file(s){_sk}")

    return results

def _prov_src_root(args):
    """The folder this run's sources live in (see _run_collapses_structure)."""
    return args.input.parent if args.input.is_file() else args.input


def _provenance_filter(pairs, mode, decode_lossless=False,
                       output_arg=None, source_root=None):
    """Drop pairs whose output already exists and came from a DIFFERENT source.

    Only meaningful in the folder-collapsing modes with DELETE_SOURCE armed:
    elsewhere the output path is derived from the source's own folder, and
    without deletion an overwrite is recoverable.

    decode_lossless: the JXL -> JPEG recovery path, whose output must stay
    byte-identical and therefore carries no marker. checksums.md5 holds the
    ORIGINAL JPEG's hash keyed by the JXL, so an existing output matching it
    came from this source — that is a stronger proof than any marker.

    Returns (kept_pairs, refused) — refused is [(src, out, why)].
    """
    if not (DELETE_SOURCE and _run_collapses_structure(mode, output_arg, source_root)):
        return pairs, []
    existing = sorted({out for _s, out in pairs if out.exists()})
    if not existing:
        return pairs, []
    logger.info(f"Provenance: checking {len(existing)} existing output(s) "
                f"(--provenance {PROVENANCE_CHECK})...")
    marks = {} if decode_lossless else _read_source_markers_batch(existing)
    kept, refused = [], []
    for src, out in pairs:
        if not out.exists():
            kept.append((src, out))
            continue
        if decode_lossless:
            stored = read_md5_db(src)
            if stored is not None and stored == md5_of_file(out):
                kept.append((src, out))
                continue
            why = ("no checksum to prove it" if stored is None
                   else "the existing JPEG is not the one this JXL holds")
        else:
            info = marks.get(str(out)) or {"src": None, "srcsum": None}
            if _provenance_ok(info, src, PROVENANCE_CHECK):
                kept.append((src, out))
                continue
            why = ("no provenance marker (written by an older version)"
                   if not (info.get("src") or info.get("srcsum"))
                   else "it was made from a different source")
        refused.append((src, out, why))
    if refused:
        logger.error(
            f"REFUSING {len(refused)} file(s): their output already exists and did "
            f"not come from them. Converting would overwrite someone else's output, "
            f"and --delete-source would then destroy what it held.")
        for _s, _o, _w in refused[:10]:
            logger.error(f"    {_s}")
            logger.error(f"      -> {_o} ({_w})")
        if len(refused) > 10:
            logger.error(f"    ... and {len(refused) - 10} more")
        logger.error(
            "  These were NOT converted and NOTHING was deleted. Rename them, pick a "
            "mode that keeps folder structure (0/1/3/8), or drop --delete-source." +
            ("" if (decode_lossless or PROVENANCE_CHECK == "content") else
             " If you MOVED the sources, re-run with --provenance content."))
    return kept, refused


def _delete_extras() -> dict:
    """Deletion counts for the run summary, so the wrapper's manifest recap can
    report the most destructive thing the tool does instead of staying silent."""
    return {
        "Sources deleted": _delete_stats["deleted"],
        "Sources deleted (already archived)": _delete_stats["deleted_archived"],
        "Sources KEPT by a delete gate": _delete_stats["kept"],
    }


def _log_delete_summary() -> None:
    if not DELETE_SOURCE:
        return
    logger.info(f"Sources DELETED: {_delete_stats['deleted']}"
                + (f" ({_delete_stats['deleted_archived']} already archived)"
                   if _delete_stats["deleted_archived"] else "")
                + f" | kept by a gate: {_delete_stats['kept']}")


def _apply_staging_args(args) -> None:
    """Resolve the effective staging directory and sweep it when asked.

    Two rules all three cmd_* entry points share, which is why they live here
    rather than being repeated (and drifting) three times:

      * `--staging` OVERRIDES the TEMP2_DIR script setting; its ABSENCE must not
        erase it. The old unconditional `TEMP2_DIR = args.staging` made the
        documented setting dead code (README: "Set TEMP2_DIR to SSD when source
        is on HDD").
      * `--clean-staging` sweeps the EFFECTIVE directory, and never on a dry
        run: the leftovers it deletes are the failed outputs the KEEP path
        deliberately preserved, and a simulation must not touch the disk.

    Call AFTER setup_logger(): on the module-level logger these messages have no
    handler, so the INFO lines vanish and the warnings land on raw stderr.
    """
    global TEMP2_DIR
    if args.staging is not None:
        TEMP2_DIR = args.staging
    if not getattr(args, "clean_staging", False):
        return
    if TEMP2_DIR is None:
        logger.warning("--clean-staging: no staging directory configured; nothing to clean")
    elif getattr(args, "dry_run", False):
        logger.info("--clean-staging: skipped (dry run); re-run without --dry-run to sweep")
    else:
        _clean_staging(TEMP2_DIR)


def cmd_transcode(args, auto_decode: bool = False):
    """Returns (errors, cancelled): error count and whether the user declined
    the delete confirmation."""
    global _counter, STORE_MD5, DELETE_SOURCE, TEMP2_DIR
    _counter = {"done": 0, "total": 0}

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
    _apply_staging_args(args)

    # A stale staging checksums.md5 from a crashed previous run would leak
    # wrong entries into this run's destination folders — start clean. Skipped
    # on a dry run for the same reason as the sweep above: it is a deletion.
    if TEMP2_DIR is not None and not args.dry_run:
        try:
            stale = Path(TEMP2_DIR) / CHECKSUMS_FILENAME
            if stale.exists():
                stale.unlink()
        except OSError:
            pass

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
        # An empty run still owes the wrapper a summary: without one the
        # manifest recap prints "(no summary - ok)" in red, whose documented
        # meaning is "the child crashed, was killed, or never launched".
        record_summary(ok=0, overwritten=0, skipped=0, errors=0,
                       log_file=log_file, dry_run=args.dry_run)
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
    pairs, _refused = _provenance_filter(
        pairs, args.mode, decode_lossless=decode,
        output_arg=args.output, source_root=_prov_src_root(args))
    if _refused:
        _counter["total"] = len(pairs)

    if args.dry_run:
        for f, out in pairs:
            logger.info(f" DRY | {f.name} -> {out}")
        logger.info(f"Dry run: {len(pairs)} files would be processed.")
        # Returning without this left emit_summary_json printing the UNTOUCHED
        # default (dry_run=false, ok=0, log=""), so the wrapper's recap showed a
        # simulation as a finished real run with zeros and never printed its
        # [DRY RUN] banner. The encoder and decoder report the planned count.
        record_summary(ok=len(pairs), overwritten=0, skipped=0, errors=0,
                       log_file=log_file, dry_run=True)
        return (0, False)

    # Create the mode-2 output dir only for real runs (dry-run must not write)
    if args.mode == 2 and not args.input.is_file():
        output_root.mkdir(parents=True, exist_ok=True)

    # Group by output folder
    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))

    # Charged for EVERY mode: deletion is a separate opt-in from the layout.
    if DELETE_SOURCE:
        if DELETE_CONFIRM:
            # Transcode is lossless in both directions (decode requires the jbrd
            # box, checked per file in decode_one_transcode), so the simple
            # 'yes' confirmation applies — the HHMM lossy confirmation is only
            # for lossy converts (cmd_convert/cmd_auto).
            if not confirm_deletion_jpeg():
                logger.info("Deletion not confirmed -- exiting.")
                return (0, True)

    logger.info(f"Output groups: {len(groups)}")

    ok = skipped = overwritten = md5_fail = aborted = 0
    # A refusal is a FAILURE, not a quiet skip: the file was not converted and
    # needs a human, so it must reach the exit code and the wrapper's recap.
    err = len(_refused)
    _reset_abort()  # a fresh run must not inherit a previous one's latch
    # Which files actually failed, for the wrapper's end-of-run FAILURES list.
    failed_files = [(str(_s), f"refused: output {_o} already exists and {_w}")
                    for _s, _o, _w in _refused]
    # ONE pool for the whole run: feeding it folder by folder meant a folder
    # with fewer files than --workers could never fill it, and every folder
    # boundary drained it. The staging move is still per-folder and still in
    # bulk (see process_group_transcode).
    results = process_group_transcode(pairs, args.workers, decode,
                                      not args.no_verify, args.mode, reconvert_explicit,
                                      smart_mode, args.effort)

    for result in results:
        status = result[1]
        if status == "ok":
            ok += 1
        # Counted apart from BOTH skipped and errors: the run gave
        # up before these were tried, so they are neither a policy
        # decision nor a failure.
        elif status == "aborted":
            aborted += 1
        elif status == "reconvert":
            ok += 1
            overwritten += 1
        elif status == "skipped":
            skipped += 1
        elif status == "md5_fail":
            err += 1
            md5_fail += 1
            failed_files.append((str(result[0]), "MD5 verification failed"))
        elif status == "error":
            err += 1
            failed_files.append((str(result[0]), str(result[2])))

    logger.info(f"\n{'-'*50}")
    if decode and md5_fail:
        logger.info(f"Done: {ok} OK | {skipped} skipped | {err} errors ({md5_fail} MD5 failures)")
    else:
        logger.info(f"Done: {ok} OK | {overwritten} reconverted | {skipped} up to date | {err} errors")
    _log_delete_summary()
    logger.info(f"Log: {log_file}")
    if _aborted():
        logger.error(f"RUN ABORTED: {_aborted()}")
        logger.error(f"  {aborted} file(s) were never attempted (not failures).")
    record_summary(ok=ok, overwritten=overwritten, skipped=skipped, errors=err,
                   log_file=log_file, failures=failed_files,
                   extras={"MD5 failures": md5_fail,
                           "Not attempted (run aborted)": aborted,
                           **_delete_extras()})
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
    # The pool submits every task up front, so a run that has given up cannot
    # stop scheduling — it stops HERE instead. Queued files return untouched
    # and are reported as "not attempted", never as errors.
    _why = _aborted()
    if _why:
        return (str(src_path), "aborted", _why, None)

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
        try:
            _run_exiftool_argfile(
                ["-overwrite_original"] + _provenance_marker_args(src_path)
                + [str(write_path)], timeout=60)
        except Exception as _e_prov:
            logger.debug(f"Provenance marker skipped: {_e_prov}")

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
        # Was the disk the real cause? The codec exits 0 while writing a
        # truncated file when the volume is full, so this arrives as an
        # integrity failure with nothing pointing at the drive.
        try:
            _need = src_path.stat().st_size
        except OSError:
            _need = 0
        _abort_if_disk_full(write_path.parent, _need)
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
    # The pool submits every task up front, so a run that has given up cannot
    # stop scheduling — it stops HERE instead. Queued files return untouched
    # and are reported as "not attempted", never as errors.
    _why = _aborted()
    if _why:
        return (str(jxl_path), "aborted", _why, None)

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
        try:
            _run_exiftool_argfile(
                ["-overwrite_original"] + _provenance_marker_args(jxl_path)
                + [str(actual_out)], timeout=60)
        except Exception as _e_prov:
            logger.debug(f"Provenance marker skipped: {_e_prov}")

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
        # Was the disk the real cause? The codec exits 0 while writing a
        # truncated file when the volume is full, so this arrives as an
        # integrity failure with nothing pointing at the drive.
        try:
            _need = jxl_path.stat().st_size
        except OSError:
            _need = 0
        _abort_if_disk_full(write_path.parent, _need)
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

    moved_finals = set()

    def _move_dest_from_staging(dest_tasks, status_map):
        """Bulk-move one destination folder's outputs out of staging."""
        moved = 0
        for src, write_out, final_out in dest_tasks:
            status = status_map.get(str(src), "error")
            if status in ("ok", "reconvert", "overwrite") and write_out.exists():
                # A locked/readonly destination must not abort the whole batch:
                # the file stays in staging and is logged for manual recovery.
                if _promote_from_staging(write_out, final_out):
                    moved += 1
                    moved_finals.add(os.path.normcase(str(final_out)))
            elif write_out.exists():
                # Failed conversion: do not promote a partial/corrupt file to the
                # final destination. Leave it in staging so the user can inspect.
                logger.warning(f"Staging: not promoting {write_out.name} (status={status})")
        if moved:
            logger.info(f" -> Moved {moved} file(s) from staging to {dest_tasks[0][2].parent}")

    # Per-destination bookkeeping so a folder can be flushed the moment its last
    # file lands, instead of stalling the pool at every folder boundary.
    tasks_by_dest = {}
    pending_by_dest = {}
    for task in tasks:
        dest = task[2].parent
        tasks_by_dest.setdefault(dest, []).append(task)
        pending_by_dest[dest] = pending_by_dest.get(dest, 0) + 1

    results = []
    status_map = {}
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
                result = fut.result()
            except Exception as e:
                # An exception escaped the worker entirely — one bad file must
                # not kill the whole batch.
                n, total = next_count()
                logger.error(f"[{n}/{total}] ERROR | {task[0].name} | {e}")
                result = (str(task[0]), "error", str(e), None)
            results.append(result)
            status_map[result[0]] = result[1]

            # Flush this destination folder once every one of its files is done.
            # Runs on the main thread (inside the as_completed loop), so no two
            # moves ever race for the same file.
            dest = task[2].parent
            pending_by_dest[dest] -= 1
            if use_staging and pending_by_dest[dest] == 0:
                _move_dest_from_staging(tasks_by_dest[dest], status_map)

    # moved_finals lets the mode-8 delete gate distinguish "this run's output
    # arrived at the final path" from a stale pre-existing file whose
    # overwrite FAILED (the gate's integrity check would otherwise certify
    # the old file and delete the source anyway).
    return results, moved_finals

def cmd_convert(args, from_jxl: bool = True):
    """Returns (errors, cancelled)."""
    global _counter, TEMP2_DIR, DELETE_SOURCE
    _counter = {"done": 0, "total": 0}

    # NOTE: the --icc-profile guards live AFTER direction auto-detection
    # below (a --force-convert on a JXL folder flips from_jxl at file
    # collection; validating here would check the wrong direction).

    smart_mode = args.sync
    reconvert_explicit = args.overwrite

    # Handle DELETE_SOURCE from CLI
    if args.delete_source:
        DELETE_SOURCE = True

    log_file = setup_logger()
    _apply_staging_args(args)
    _warn_distance_clamp(args.distance)

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
        # See cmd_transcode: an empty run still owes the wrapper a summary.
        record_summary(ok=0, overwritten=0, skipped=0, errors=0,
                       log_file=log_file, dry_run=args.dry_run)
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
    if direction == "from_jxl" and (args.format or "jpeg") in ("jpeg", "jpg") and args.bit_depth == 16:
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
    pairs, _refused = _provenance_filter(
        pairs, args.mode,
        output_arg=args.output, source_root=_prov_src_root(args))
    if _refused:
        _counter["total"] = len(pairs)

    if args.dry_run:
        for f, out in pairs:
            logger.info(f" DRY | {f.name} -> {out}")
        logger.info(f"Dry run: {len(pairs)} files would be converted.")
        # Same rule as cmd_transcode: without this the wrapper reads the
        # untouched default and reports the simulation as a real run.
        record_summary(ok=len(pairs), overwritten=0, skipped=0, errors=0,
                       log_file=log_file, dry_run=True)
        return (0, False)

    # Create the mode-2 output dir only for real runs (dry-run must not write)
    if args.mode == 2 and not args.input.is_file():
        output_root.mkdir(parents=True, exist_ok=True)

    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))

    logger.info(f"Output groups: {len(groups)}")

    # Safety confirmation for DELETE_SOURCE, in every mode
    # Determine if operation is lossy based on direction and settings
    if DELETE_SOURCE:
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

    ok = skipped = overwritten = aborted = 0
    err = len(_refused)
    _reset_abort()  # a fresh run must not inherit a previous one's latch
    # Which files actually failed, for the wrapper's end-of-run FAILURES list.
    failed_files = [(str(_s), f"refused: output {_o} already exists and {_w}")
                    for _s, _o, _w in _refused]
    # ONE pool for the whole run (see process_group_convert): the per-folder
    # loop this replaces could never fill the pool from a small folder and
    # drained it at every boundary.
    results, moved_finals = process_group_convert(
        pairs, args.workers, direction,
        args.quality, args.distance, args.format, args.bit_depth,
        args.icc_profile, args.ram, args.effort, reconvert_explicit,
        False, smart_mode
    )

    # Handle DELETE_SOURCE for convert mode (lossy), in every mode
    if DELETE_SOURCE:
        deleted = 0
        src_map = {str(s): (s, out) for s, out in pairs}
        for result in results:
            status = result[1]
            # LOSSY direction: see the note in _process_file_group — the
            # structural check is the whole gate for an already-archived source.
            was_skipped = status == "skipped"
            if (status not in ("ok", "reconvert")
                    and not (DELETE_SKIPPED and was_skipped)):
                continue
            src_path, final_file = src_map.get(result[0], (None, None))
            if src_path is None:
                continue
            # Fail closed on a failed staging move: the file at the final
            # path would be a stale pre-existing one, not this run's output.
            if (TEMP2_DIR is not None and not was_skipped
                    and os.path.normcase(str(final_file)) not in moved_finals):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (output never left staging) | {src_path.name}")
                continue
            if final_file is None or not _verify_file_integrity(final_file):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (output failed integrity check) | {src_path.name}")
                continue
            try:
                src_path.unlink()
                deleted += 1
                _delete_stats["deleted"] += 1
                if was_skipped:
                    _delete_stats["deleted_archived"] += 1
                logger.info(f" DELETED source"
                            f"{' (already archived)' if was_skipped else ''}"
                            f" | {src_path.name}")
            except OSError as e:
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (could not delete source) | {src_path.name}: {e}")
        if deleted:
            logger.info(f" -> Deleted {deleted} source file(s)")

    for src, status, detail, _ in results:
        if status == "ok":
            ok += 1
        # Counted apart from BOTH skipped and errors: the run gave
        # up before these were tried, so they are neither a policy
        # decision nor a failure.
        elif status == "aborted":
            aborted += 1
        elif status == "reconvert":
            ok += 1
            overwritten += 1
        elif status == "skipped":
            skipped += 1
        elif status == "error":
            err += 1
            failed_files.append((str(src), str(detail)))

    logger.info(f"\n{'-'*50}")
    logger.info(f"Done: {ok} OK | {overwritten} reconverts | {skipped} skipped | {err} errors")
    if _aborted():
        logger.error(f"RUN ABORTED: {_aborted()}")
        logger.error(f"  {aborted} file(s) were never attempted (not failures).")
    _log_delete_summary()
    logger.info(f"Log: {log_file}")
    record_summary(ok=ok, overwritten=overwritten, skipped=skipped, errors=err,
                   log_file=log_file, failures=failed_files,
                   extras={"Not attempted (run aborted)": aborted, **_delete_extras()})
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

    # Handle --no-md5 (was silently ignored on this path)
    if args.no_md5:
        STORE_MD5 = False

    # Handle DELETE_SOURCE from CLI (same as cmd_transcode/cmd_convert)
    if args.delete_source:
        DELETE_SOURCE = True

    log_file = setup_logger()
    _apply_staging_args(args)
    _warn_distance_clamp(args.distance)

    # A stale staging checksums.md5 from a crashed previous run would leak
    # wrong entries into this run's destination folders — start clean. Skipped
    # on a dry run for the same reason as the sweep: it is a deletion.
    if TEMP2_DIR is not None and not args.dry_run:
        try:
            stale = Path(TEMP2_DIR) / CHECKSUMS_FILENAME
            if stale.exists():
                stale.unlink()
        except OSError:
            pass
    
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

    # --from-jpeg: the mirror image — restrict the encode direction to JPEG
    # sources and leave PNGs untouched. Honoured here too, not just in convert
    # mode: silently ignoring a direction flag would convert (and, under
    # --mode 8, delete) PNGs the user explicitly scoped out.
    elif getattr(args, "from_jpeg", False):
        if png_files:
            logger.info(f"--from-jpeg: ignoring {len(png_files)} PNG file(s)")
        png_files = []

    if not jpeg_files and not png_files and not jxl_files:
        logger.warning("No JPEG, PNG or JXL files found.")
        # See cmd_transcode: an empty run still owes the wrapper a summary.
        record_summary(ok=0, overwritten=0, skipped=0, errors=0,
                       log_file=log_file, dry_run=args.dry_run)
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
    # args.format is None when --format was not passed (default is JPEG), so
    # the check must evaluate the EFFECTIVE format, and "jpg" too.
    if (args.format or "jpeg") in ("jpeg", "jpg") and args.bit_depth == 16:
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
    # Skipped on dry runs (nothing is converted, so nothing would be deleted)
    # and when DELETE_CONFIRM is off. Charged in every mode.
    if DELETE_SOURCE and not args.dry_run and DELETE_CONFIRM:
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
    totals = {"ok": 0, "err": 0, "skipped": 0, "aborted": 0, "overwritten": 0}
    _reset_abort()  # once per RUN, not per group: the four _process_file_group
                    # calls below must share one latch, so an abort in the first
                    # stops the rest instead of being forgotten between them.
    # Failure paths ride alongside the counts (kept out of `totals` so the
    # numeric merge below stays a plain sum).
    all_failures = []

    def _merge(tally):
        for k in totals:
            totals[k] += tally.get(k, 0)
        all_failures.extend(tally.get("failures", []))

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
                f"{totals['ok']} OK | {totals['overwritten']} reconverted | "
                f"{totals['skipped']} skipped | {totals['err']} errors")
    if _aborted():
        logger.error(f"RUN ABORTED: {_aborted()}")
        logger.error(f"  {totals['aborted']} file(s) were never attempted (not failures).")
    _log_delete_summary()
    logger.info(f"Log: {log_file}")
    record_summary(
        # A dry run converts nothing, so `totals` is all zeros — report the
        # PLANNED output count instead, matching what the encoder/decoder put
        # in their dry-run summaries. Otherwise the wrapper's recap shows a
        # simulation of 5000 files as a row of zeros.
        ok=len(all_pairs) if args.dry_run else totals["ok"],
        overwritten=totals["overwritten"], skipped=totals["skipped"],
        errors=totals["err"], log_file=log_file,
        failures=all_failures, dry_run=args.dry_run,
        extras={"Not attempted (run aborted)": totals["aborted"], **_delete_extras()})
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
    pairs, _refused = _provenance_filter(
        pairs, args.mode,
        decode_lossless=(use_transcode and all(
            s.suffix.lower() == '.jxl' for s, _o in pairs)),
        output_arg=args.output, source_root=_prov_src_root(args))
    if _refused:
        _counter["total"] = max(0, _counter.get("total", 0) - len(_refused))
    # A refusal is a FAILURE, not a quiet skip: the file was not converted and
    # needs a human, so it must reach the exit code and the wrapper's recap —
    # exactly as cmd_transcode and cmd_convert already do. cmd_auto only ever
    # subtracted the refusals from the progress total, so an auto run that
    # refused EVERY file exited 0 with an empty failure list: a scheduled job
    # saw a clean run and only the log said otherwise.
    _refused_tally = {
        "ok": 0, "err": len(_refused), "skipped": 0, "aborted": 0, "overwritten": 0,
        "failures": [(str(_s), f"refused: output {_o} already exists and {_w}")
                     for _s, _o, _w in _refused],
    }

    if args.dry_run:
        for f, out in pairs:
            logger.info(f" DRY | {f.name} -> {out}")
        if DELETE_SOURCE:
            logger.warning(
                f"Dry run: --delete-source is ARMED. Up to {len(pairs)} source "
                f"file(s) in this group would be DELETED.")
        # Zeros, like cmd_transcode's and cmd_convert's dry runs: a simulation
        # does not fail. _provenance_filter has already logged each refusal, so
        # they are not invisible. (Whether a dry run should report the refusals
        # it PREDICTS is a separate question, open for all three commands.)
        return {"ok": 0, "err": 0, "skipped": 0}

    if not pairs:
        # Everything in this group was refused. Returning zeros here was the
        # other half of the same bug.
        return _refused_tally

    tally = dict(_refused_tally)
    tally["failures"] = list(_refused_tally["failures"])

    def _accumulate(results):
        for r in results:
            st = r[1]
            if st in ("ok", "reconvert", "overwrite"):
                tally["ok"] += 1
                # Counted as well as folded into ok, exactly like cmd_transcode
                # and cmd_convert do. Without this cmd_auto reported
                # overwritten=0 on every run, so the wrapper's manifest recap
                # showed "ovw 0" for a pass that reconverted the whole folder.
                if st in ("reconvert", "overwrite"):
                    tally["overwritten"] += 1
            elif st == "skipped":
                tally["skipped"] += 1
            elif st == "aborted":
                # Named explicitly: the catch-all below calls everything else
                # an error, which would turn every never-attempted file into a
                # reported failure — the exact wall of noise the abort removes.
                tally["aborted"] += 1
            else:
                tally["err"] += 1
                # r[0] is the source path; r[2] is the reason (or the output
                # path for md5_fail, which has no message of its own).
                reason = "MD5 verification failed" if st == "md5_fail" else str(r[2])
                tally["failures"].append((str(r[0]), reason))

    # ONE pool per direction for the whole group, not one per output FOLDER:
    # auto mode runs on mixed libraries where modes 3/5/6/7 create a folder per
    # shoot, and the old per-folder loop could never fill the pool from any of
    # them. process_group_* still flushes staging per folder.
    if use_transcode:
        # Separate JPEG encode vs JXL decode: they call different workers.
        encode_pairs = [(s, f) for s, f in pairs if s.suffix.lower() in ('.jpg', '.jpeg', '.jfif', '.jpe')]
        decode_pairs = [(s, f) for s, f in pairs if s.suffix.lower() == '.jxl']
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
        results, moved_finals = process_group_convert(
            pairs, args.workers, direction=direction,
            quality=args.quality, distance=args.distance,
            fmt=args.format or "jpeg",
            bit_depth=args.bit_depth or (default_depth if direction == "from_jxl" else 8),
            output_icc=args.icc_profile, use_ram=args.ram,
            effort=args.effort, reconvert_val=args.overwrite,
            use_internal_srgb=False, smart=args.sync
        )
        _accumulate(results)
        # Handle DELETE_SOURCE for lossy convert (auto mode), in every mode
        if DELETE_SOURCE:
            deleted = 0
            src_map = {str(s): (s, out) for s, out in pairs}
            for result in results:
                status = result[1]
                # LOSSY direction: nothing is stored and nothing can be
                # re-derived, so an already-archived source is judged by the
                # STRUCTURAL check alone. There is no way to prove that output
                # came from that source — main() warns and the wrapper asks
                # for its own confirmation.
                was_skipped = status == "skipped"
                if (status not in ("ok", "reconvert")
                        and not (DELETE_SKIPPED and was_skipped)):
                    continue
                src_path, final_file = src_map.get(result[0], (None, None))
                if src_path is None:
                    continue
                # Fail closed on a failed staging move: the file at the
                # final path would be a stale pre-existing one.
                if (TEMP2_DIR is not None and not was_skipped
                        and os.path.normcase(str(final_file)) not in moved_finals):
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (output never left staging) | {src_path.name}")
                    continue
                if final_file is None or not _verify_file_integrity(final_file):
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (output failed integrity check) | {src_path.name}")
                    continue
                try:
                    src_path.unlink()
                    deleted += 1
                    _delete_stats["deleted"] += 1
                    if was_skipped:
                        _delete_stats["deleted_archived"] += 1
                    logger.info(f" DELETED source"
                                f"{' (already archived)' if was_skipped else ''}"
                                f" | {src_path.name}")
                except OSError as e:
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (could not delete source) | {src_path.name}: {e}")
            if deleted:
                logger.info(f" -> Deleted {deleted} source file(s)")

    return tally

# --------------------------------------------─
# AUTO MODE (Per-file detection for directories)
# --------------------------------------------─
# MAIN ENTRY POINT (Auto-routing)
# --------------------------------------------─

def build_parser():
    """The CLI parser, split out of main() so tests can build an args namespace
    without going through sys.argv (and without duplicating 90 add_argument
    calls that would then drift)."""
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
    # choices, like the encoder and decoder: without it --mode 9 sailed through
    # argparse and died deep inside resolve_output_transcode with a raw
    # ValueError traceback, after the log header and "Files found: N" had
    # already been printed. default stays None — main() picks the per-command
    # default (TRANSCODE_DEFAULT_MODE / CONVERT_DEFAULT_MODE) from it.
    parser.add_argument("--mode", type=int, default=None, choices=range(9),
                        help="Output mode 0-8 (0=in-place, 1=subfolder, etc)")
    parser.add_argument("--workers", type=int, default=min(os.cpu_count() or 4, 16),
                        help="Parallel workers")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    parser.add_argument("--sync", action="store_true",
                        help="Smart mode: only process if source is newer than destination")
    parser.add_argument("--clean-staging", action="store_true",
                        help="Before converting, delete staging leftovers from EARLIER "
                             "runs (failed outputs kept for inspection). Files touched in "
                             "the last hour are left alone: a concurrent run may own them")
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
    parser.add_argument("--delete-source", action="store_true",
                        help="Delete source after a verified conversion, in ANY mode. "
                             "IRREVERSIBLE")
    parser.add_argument("--provenance", type=str, default=None,
                        choices=["path", "content"],
                        help="[with --delete-source, modes 2/4/5/6/7] How an EXISTING "
                             "output is matched to the source about to overwrite it: "
                             "path (default, free) compares the recorded LOCATION; "
                             "content also accepts matching source bytes, so it "
                             "survives MOVED folders. The lossless JXL->JPEG path "
                             "uses checksums.md5 instead, since its output must stay "
                             "byte-identical and cannot carry a marker.")
    parser.add_argument("--delete-skipped", action="store_true",
                        help="[with --delete-source] Also delete sources whose output "
                             "ALREADY EXISTS (reported as SKIP), so an archive interrupted "
                             "between the conversion and the unlink can be finished. "
                             "Lossless JPEG<->JXL: provenance is PROVEN against "
                             "checksums.md5. LOSSY directions: structural check only — "
                             "nothing can prove that output came from that source")
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
                        help="[Convert/Auto mode] Restrict JPEG->JXL conversion to JPEG "
                             "files only (PNGs in the folder are left untouched).")
    parser.add_argument("--effort", type=int, default=CJXL_EFFORT, choices=range(1, 11),
                        help="cjxl effort 1-10")

    # Convert specific
    parser.add_argument("--ram", action="store_true", default=True, help="Accepted for CLI compatibility; decode currently always uses temporary files (no in-RAM pipeline yet)")
    parser.add_argument("--no-ram", dest="ram", action="store_false", help="Use disk")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--summary-json", action="store_true",
                        help=argparse.SUPPRESS)  # internal: machine-readable summary for jxl_photo.py
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

    return parser


def main():
    parser = build_parser()
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

    # Range checks the encoder has done all along. Without them --distance 99
    # and --quality 500 sailed through argparse and failed inside cjxl/djxl
    # once PER FILE, with the real cause named nowhere. Exit 2 = "aborted /
    # invalid arguments", matching the documented exit-code table.
    if not 0 <= args.distance <= 15:
        print("ERROR: --distance must be between 0 and 15")
        sys.exit(2)
    if not 1 <= args.quality <= 100:
        print("ERROR: --quality must be between 1 and 100")
        sys.exit(2)

    # Normalize format: jpg -> jpeg
    if args.format == "jpg":
        args.format = "jpeg"

    # Apply configurable export marker before resolving outputs
    global EXPORT_MARKER, EXPORT_JPEG_SUBFOLDER, DELETE_CONFIRM, DELETE_SKIPPED, DELETE_SOURCE, PROVENANCE_CHECK
    if args.export_marker:
        EXPORT_MARKER = args.export_marker
    if args.export_subfolder is not None:
        EXPORT_JPEG_SUBFOLDER = args.export_subfolder
    if args.delete_confirm_off:
        DELETE_CONFIRM = False
    if args.delete_source:
        DELETE_SOURCE = True
    if args.delete_skipped:
        DELETE_SKIPPED = True
    if args.provenance is not None:
        PROVENANCE_CHECK = args.provenance

    if DELETE_SKIPPED and not DELETE_SOURCE:
        print("WARNING: --delete-skipped has no effect without --delete-source: it only "
              "widens which sources the deletion covers. Nothing will be deleted.")
    elif DELETE_SKIPPED:
        # The strength of this flag depends entirely on the direction, and the
        # difference is big enough that it has to be said out loud. setup_logger()
        # runs inside each cmd_*, so print() is the right channel here.
        print("NOTE: --delete-skipped will delete sources whose output already exists.")
        print("      Lossless JPEG<->JXL: provenance is PROVEN against checksums.md5 "
              "(the stored hash must match).")
        print("      LOSSY directions (JXL -> JPEG/PNG, lossy encodes): STRUCTURAL CHECK "
              "ONLY. Nothing can prove")
        print("      that the existing output came from that source -- an unrelated file "
              "with the same name would pass.")

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

    # Modes 6/7 anchor on an EXPORT folder and scan recursively; over a single
    # FILE the scan yields nothing and the run exits 0 having done nothing.
    if args.mode in (6, 7) and args.input.is_file():
        print(f"ERROR: --mode {args.mode} needs a DIRECTORY: it scans the folder "
          f"tree for the export marker. Got a file.")
        sys.exit(2)

    # Required tools, once, before any cmd_* runs. Without this a missing
    # cjxl/djxl/exiftool turns into N cryptic per-file FileNotFoundError
    # instead of one clear message. (setup_logger() runs inside each cmd_*,
    # so print() is the right channel here.)
    if not args.dry_run:
        _missing = [n for n in ("cjxl", "djxl") if shutil.which(n) is None]
        if shutil.which(_get_exiftool_cmd()) is None:
            _missing.append("exiftool")
        if _missing:
            print(f"ERROR: required tool(s) not found in PATH: {', '.join(_missing)}")
            print("  libjxl (cjxl/djxl): https://github.com/libjxl/libjxl/releases")
            print("  exiftool:           https://exiftool.org")
            sys.exit(1)
        _v = _tool_version("cjxl")
        if _v is not None and _v[:2] < (0, 11):
            print(f"WARNING: cjxl {'.'.join(map(str, _v))} is older than the supported "
                  f"minimum (0.11.2); conversions may fail in confusing ways.")

    # NOTE: --clean-staging runs inside each cmd_* (see _apply_staging_args),
    # not here: it needs the EFFECTIVE staging dir (which may come from the
    # TEMP2_DIR script setting, not just --staging) and it must never run on a
    # dry run — sweeping is a real deletion of the outputs the KEEP path
    # deliberately preserved for inspection.

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

    # Emitted BEFORE the exits below: a run with failures is exactly the run the
    # wrapper most needs the summary from. A cancelled run recorded nothing, so
    # the wrapper sees no summary for that entry and labels it accordingly.
    if not cancelled:
        emit_summary_json(args.summary_json)

    if cancelled:
        sys.exit(3)

    # Whatever this run could not move out is still sitting on the staging
    # drive; saying so is what keeps the leak from being invisible. Reports the
    # EFFECTIVE directory: staging can come from the TEMP2_DIR script setting
    # with no --staging flag in sight.
    _report_staging_leftovers(TEMP2_DIR)

    # Exit 2 = aborted, and it outranks exit 1: a run that gave up early is not
    # the same event as a run that finished with some bad files, and automation
    # has to be able to tell them apart (the aborted one is worth retrying).
    if _aborted():
        sys.exit(2)

    if errors:
        sys.exit(1)

if __name__ == "__main__":
    main()