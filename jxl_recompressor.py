#!/usr/bin/env python3
"""
jxl_recompressor.py — Batch JPEG XL -> JPEG XL recompressor (smaller archives, same metadata)

Re-encodes JXL files at a new distance/effort with `cjxl in.jxl out.jxl`, keeping
everything this toolkit stamps into an archive: the base64 ICC in XMP CreatorTool,
EXIF/XMP metadata, provenance markers (jxlphoto-*) and multi-page group markers.

If a JXL was written by this toolkit it carries its encoding parameters
("cjxl d=X e=Y" in XMP-dc:Description or EXIF Software). The recompressor reads
them and compares against the requested ones, so a choice that cannot gain
anything (same distance, or a LOWER distance than an already-lossy source) is
caught: the default policy then copies the original instead of paying a
generation of lossy re-encode for nothing.

Usage:
  py jxl_recompressor.py <input> [output] --mode 0-8 --distance 1.0 [--workers N]

Requirements:
  cjxl / djxl  ->  https://github.com/libjxl/libjxl/releases
  exiftool     ->  https://exiftool.org
  numpy + imagecodecs (only for --verify-roundtrip)
"""

import subprocess, os, platform, tempfile, threading, logging, sys, shutil, uuid, hashlib, json
import math
import re
import functools
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time
from typing import Any, Dict, Optional
import argparse

import numpy as np

# Module-level logger; setup_logger() replaces it with a configured instance when main() runs.
logger = logging.getLogger("jxl_recompress")


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
    """Clear the latch. Called when a run starts (and by the tests).

    Also clears the per-run delete counters: they are run state exactly like the
    latch, and a second run in the SAME process (the test suite, or anything
    importing this module) inherited the first one's totals — so the summary
    reported deletions that this run never made.
    """
    global _abort_reason
    with _abort_lock:
        _abort_reason = None
    for _k in _delete_stats:
        _delete_stats[_k] = 0


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

    But "the move raised" does not mean "the destination is partial". The move
    can fail BEFORE touching the destination (a read-only or locked
    pre-existing file — that file is a perfectly good archive), and the copy
    can SUCCEED with only the staging unlink failing (the destination then
    holds the COMPLETE new output). Deleting either one destroys good data, so
    the cleanup compares against an identity snapshot taken before the move
    and removes the destination only when the move provably wrote to it AND
    what it wrote is incomplete. When in doubt, the file is kept.

    A destination volume that is simply FULL also has to stop the run rather
    than produce one MOVE FAILED line per remaining file, which is what the
    disk-full abort exists for.
    """
    pre_identity = None
    try:
        _st = final_path.stat()
        pre_identity = (_st.st_mtime_ns, _st.st_size)
    except OSError:
        pass
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
                _st = final_path.stat()
                written = (pre_identity is None
                           or (_st.st_mtime_ns, _st.st_size) != pre_identity)
                complete = size > 0 and _st.st_size == size
            except OSError:
                written, complete = False, False  # cannot tell → keep the file
            if written and not complete:
                try:
                    final_path.unlink()
                    logger.error(
                        f"    Removed the partial file left at the destination"
                        + (" (it had OVERWRITTEN an existing output, which was already "
                           "corrupt by then)" if pre_identity is not None else "")
                        + " — the complete copy is still in staging.")
                except OSError as e2:
                    logger.error(f"    Could NOT remove the partial destination file "
                                 f"({e2}). Delete {final_path} by hand before re-running: "
                                 f"a later sync run would treat it as up to date.")
            elif written:
                logger.error(
                    "    The destination holds a COMPLETE copy (the copy itself "
                    "finished; only the staging cleanup failed) — keeping it.")
            else:
                logger.error(
                    "    The destination was never touched by the move "
                    "(pre-existing file) — keeping it.")
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


# ─────────────────────────────────────────────
# USER SETTINGS - GENERAL
# ─────────────────────────────────────────────

CJXL_DISTANCE = 1.0
# Target distance for the NEW files (0-15). This is the whole point of the
# tool: archives were written at high quality (d=0.05-0.1) and are recompressed
# smaller later when storage runs short.
# 0   = lossless re-encode (only useful from a LOSSLESS source, see below)
# 1.0 = "visually lossless" (~8MB from a 45MP/16-bit original) — typical target
# 2.0 = smaller still, still very good for screen viewing

CJXL_EFFORT = 7
# Compression effort (1-10). Controls file size and encode time, NOT quality.
# 7 is the sweet spot for camera photos.

CJXL_BUFFERING = None
# [libjxl >= 0.12 only] --buffering flag passed to cjxl. None = cjxl default.

CJXL_TIMEOUT = 900
# Per-file cjxl/djxl timeout in seconds.

TEMP_DIR = None
# Scratch dir for exiftool argfiles and --verify-roundtrip decodes.
# None = system temp.

TEMP2_DIR = None
# Staging dir (fast SSD): outputs are written there and moved to the final
# destination per file. None = write directly at the destination.

OVERWRITE = "smart"
# False      -> skip files whose output already exists
# "smart"    -> recompress only when the source JXL is newer than the output
# True       -> always recompress
# NOTE: in-place modes (8, and 0 without an output folder) never "skip" — the
# output IS the input, so their timestamps always tie. See _would_skip.

ON_DOWNGRADE = "ask"
# What to do when the requested d/e CANNOT gain anything over what the file
# already is (same distance as an already-lossy source, or a LOWER distance —
# quality cannot be recovered, the file only grows). One of:
# "ask"     -> batch prompt before the run (interactive only; unattended = skip)
# "copy"    -> copy the original JXL verbatim (zero generation loss)
# "skip"    -> leave it out of the run
# "convert" -> re-encode anyway (you were warned)

ON_REGENERATION = "ask"
# What to do when the source file is ALREADY a lossy re-encode (gen >= 1 in
# the encode record) and the request would add ANOTHER lossy generation.
# Measured on real files: each lossy re-encode costs ~1 dB regardless of how
# small the distance step is, and after generation 1 the nominal d stops
# describing quality (a 17-generation chain landed 8.5 dB below a single
# direct encode at the same file size). d_new > d_old cannot see this — it
# compares one step at a time; the generation count is what the history
# warns about. Same values as ON_DOWNGRADE, and independent of it: when both
# fire, the more conservative action wins (skip > copy > ask > convert).

ON_UNKNOWN = "convert"
# What to do with JXLs that carry NO encoding record (not written by this
# toolkit, or written with --encode-tag off), so nothing can be compared.
# Same values as ON_DOWNGRADE. Default converts: with nothing to compare
# against, the request is taken literally.

JBRD_POLICY = "copy"
# JXLs transcoded from JPEG carry a jbrd box — the ORIGINAL JPEG is bit-exact
# recoverable from them (djxl --reconstruct_jpeg), and checksums.md5 binds
# their bytes for the transcoder's delete gates. Recompressing destroys both.
# "copy"    -> copy verbatim (default: the archive keeps its JPEG recovery)
# "skip"    -> leave it out of the run
# "convert" -> re-encode anyway: the jbrd is lost and the JPEG is no longer
#              recoverable from the output. Logged per file.

KEEP_SMALLER = True
# After re-encoding, compare sizes: if the new JXL is NOT smaller than the
# source, replace it with a verbatim copy of the source (same bytes, zero
# generation loss). Protects photos that were already well compressed.
# In-place runs keep the original file instead (nothing changes).

ENCODE_TAG_MODE = "xmp"
# Where to record the NEW encoding parameters, mirroring jxl_tiff_encoder.py:
# "xmp"      -> XMP-dc:Description (default; the new cjxl d=/e= is APPENDED to
#               the existing chain with the gen= token reconciled at the head
#               of the machine block — unrelated text is kept)
# "software" -> EXIF Software field
# "off"      -> record nothing; any previous record (gen= + cjxl d=/e= chain)
#               is STRIPPED, so a later run never trusts stale parameters.
#               This is the only way to deliberately discard the lineage.

VERIFY_ROUNDTRIP = False
# Decode both the source JXL and the output and compare pixels before any
# delete/replace. Lossless->lossless requires pixel-identical decodes; anything
# involving a lossy step uses brightness + PSNR sanity floors below.
VERIFY_LOSSY_MIN_MEAN_RATIO = 0.7
VERIFY_LOSSY_MIN_PSNR = 20.0

DELETE_SKIPPED = False
# Also delete sources whose output already existed (archive interrupted between
# conversion and unlink). The existing output must still pass every gate.

DELETE_SOURCE = False
# Delete each source JXL after its output is written, verified at its FINAL
# path, and (for verbatim copies) MD5-matched. Three confirmations unless the
# wrapper already charged them (--delete-confirm-off).

PROVENANCE_CHECK = "path"
# "path"    -> an existing output is matched to the source replacing it by the
#              recorded jxlphoto-src location id (free)
# "content" -> also accepts a matching jxlphoto-srcsum (survives moved folders)
# There is no "adopt" here: recompressor outputs keep the source JXL's own
# markers verbatim, so an archive made by jxl_tiff_encoder.py is provable as-is.

# ─────────────────────────────────────────────
# USER SETTINGS - MODES CONFIGURATION
# ─────────────────────────────────────────────

CONVERTED_JXL_FOLDER = "recompressed_jxl"
# Mode 1: subfolder created inside the input folder.

JXL_FOLDER_NAME = "JXL_recompressed"
# Modes 3 and 5: folder created inside/next to each source folder.

JXL_SUFFIX_TO_REPLACE = "JXL"
JXL_SUFFIX_REPLACE = "JXL_small"
# Mode 4: folder token replaced ("16B_JXL" -> "16B_JXL_small"); when the token
# is not found, "_JXL_small" is appended instead.

EXPORT_MARKER = "_EXPORT"
# Modes 6/7: only files under a folder whose name starts/ends with this marker.

EXPORT_JXL_FOLDER = "16B_JXL_small"
# Modes 6/7: output folder created under the marker folder.

EXPORT_JXL_SUBFOLDER = ""
# Mode 7 only: only files under EXPORT_MARKER/<this subfolder> are processed;
# the subfolder level is dropped from the output path. Empty = all subfolders.

# ─────────────────────────────────────────────
# SAFETY SETTINGS
# ─────────────────────────────────────────────

DELETE_CONFIRM = True
# Ask before deleting originals. The wrapper charges its own confirmations and
# passes --delete-confirm-off.

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "Logs" / "jxl_recompressor"

counter_lock = threading.Lock()
_counter = {"done": 0, "total": 0}
# What the deletion actually did. Module-level because process_group owns the
# delete gate but main() owns the summary.
_delete_stats = {"deleted": 0, "deleted_archived": 0, "kept": 0}

# Per-file failure reason recorded by convert_one, so the run summary can say
# WHAT failed instead of a bare "error" (the reason used to exist only in the
# per-file log line, and the summary's failures list carried the status word).
_error_details = {}

# XMP dc:Relation provenance markers — the same strings the encoder writes, so
# a recompressed archive stays provable by the DECODER's delete gates.
SRC_PREFIX = "jxlphoto-src:"
SRCSUM_PREFIX = "jxlphoto-srcsum:"
# Multi-page group id, written by the encoder into every page's dc:Relation.
# Pages that share a document live or die together: the delete gate removes
# the whole group or nothing (a half-deleted group is spread across two
# folders with a dangling master page).
MULTIPAGE_XMP_MARKER = "jxlphoto-mpg:"

# The encoder's encoding-parameters tag: "cjxl d=0.1 e=7", possibly several in
# a " | "-separated chain (the LAST one is the current file's).
_ENCODE_TAG_RE = re.compile(r"cjxl\s+d=([0-9.]+)\s+e=(\d+)")

# The stored generation token, read ONLY as a complete " | "-delimited segment
# (or bounded by the field's start/end): "Project gen=3 phase 2" is running
# text, not a token, and must never be read as one.
_GEN_TAG_RE = re.compile(r"(?:^|\|)\s*gen=(\d+)\s*(?=\||$)")

# The whole machine block as ONE unit: an optional gen= lead segment followed
# by the contiguous cjxl d=/e= chain. The block only starts at a segment
# boundary ((?:^|\|)), so it never bites into a caption's running text; and
# matching only the first cjxl entry would leave an orphaned gen= and a
# partial chain behind. Legacy fields (a chain with no gen=) are the normal
# case and match in full.
_MACHINE_BLOCK_RE = re.compile(
    r"(?:^|\|)\s*"
    r"(?:gen=\d+\s*\|\s*)?"
    r"cjxl\s+d=[0-9.]+\s+e=\d+"
    r"(?:\s*\|\s*cjxl\s+d=[0-9.]+\s+e=\d+)*"
)

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


def _cjxl_buffering_flag():
    """--buffering flag for cjxl >= 0.12; empty list otherwise (flag doesn't exist there).

    Probes _get_cjxl_cmd() because THIS script has a configurable cjxl path and
    must version-check the binary it will actually run. jxl_jpeg_transcoder.py's
    copy probes the bare "cjxl" for the same reason — that is where its calls
    go. The two are deliberately not identical and are not in SHARED_HELPERS.
    """
    if CJXL_BUFFERING is not None and _tool_at_least(_get_cjxl_cmd() or "cjxl", 0, 12):
        return [f"--buffering={CJXL_BUFFERING}"]
    return []


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

    logger = logging.getLogger("jxl_recompress")
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
    """Log rejected files to Logs/jxl_recompressor/rejected_files.log for easy review."""
    try:
        rej_dir = SCRIPT_DIR / "Logs" / "jxl_recompressor"
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


def confirm_deletion_jxl(is_lossy: bool) -> bool:
    """Interactive confirmation before deleting/replacing source JXLs.
    Lossy: type the current time (HHMM) shown on screen. Lossless: type 'yes'.
    Returns True if confirmed, False if cancelled."""
    from datetime import datetime as _dt
    print()
    print()
    print()
    if is_lossy:
        print("  [!] WARNING -- source JXLs will be DESTROYED")
        print(f"     Recompressing LOSSY (distance={CJXL_DISTANCE}) -- the original")
        print("     JXL cannot be recovered from the smaller one. This is IRREVERSIBLE.")
        now   = _dt.now()
        token = now.strftime("%H%M")
        print(f"     Current time: {now.strftime('%H:%M')}  ->  to confirm, type: {token}")
        print()
        try:
            answer = input("     > ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer == token:
            print("     Confirmed. Source JXLs will be deleted/replaced after verification.")
            print()
            return True
        else:
            print("     Cancelled. No files will be deleted.")
            print()
            return False
    else:
        print("  [!] WARNING -- source JXLs will be DESTROYED")
        print("     Source JXLs will be deleted/replaced after the new file is verified.")
        print("     Type 'yes' to confirm, anything else to cancel.")
        print()
        try:
            answer = input("     > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer == "yes":
            print("     Confirmed. Source JXLs will be deleted/replaced after verification.")
            print()
            return True
        else:
            print("     Cancelled. No files will be deleted.")
            print()
            return False


# ---------------------------------------------------------------------------
# JXL box / hash utilities
# ---------------------------------------------------------------------------

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


def md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Provenance (which source made an output). Same markers as the encoder's —
# the recompressor copies them verbatim, so they still point at the ORIGINAL
# source (the TIFF), which is exactly what the decoder's delete gates compare.
# ---------------------------------------------------------------------------

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


# {(normcased path, size, mtime_ns): sha256 digest} for the run. A multi-page
# TIFF is converted one PAGE at a time and each page asked for its source's
# content id, so a 700 MB three-page scan was read 2.1 GB worth of times to
# produce the same hash three times over.
#
# Keyed on size and mtime as well as the path, so a source edited mid-run is
# never served a stale hash. No lock: two threads racing compute the SAME value
# and the second store is a no-op.
_content_id_cache = {}


def _file_digest_cached(path) -> bytes:
    """sha256 of one file's bytes, remembered for this run."""
    try:
        st = os.stat(path)
        key = (os.path.normcase(str(path)), st.st_size, st.st_mtime_ns)
    except OSError:
        key = None
    if key is not None:
        hit = _content_id_cache.get(key)
        if hit is not None:
            return hit
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    digest = h.digest()
    if key is not None:
        _content_id_cache[key] = digest
    return digest


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
        outer.update(_file_digest_cached(p))
    return outer.hexdigest()[:16]


def _read_source_markers_batch(outputs: list) -> dict:
    """{output path: {'src': id|None, 'srcsum': id|None}} in as few exiftool
    calls as possible — one per file would be minutes on a large library.

    A file whose markers cannot be read comes back with both None, which the
    caller treats as "cannot prove anything": fail closed.
    """
    markers = {str(o): {"src": None, "srcsum": None} for o in outputs}
    # normcase -> the exact key the caller will look up by.
    index = {os.path.normcase(str(o)): str(o) for o in outputs}
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
                # normcase, like every other path comparison in the provenance
                # layer: exiftool can hand back a differently-cased drive letter
                # or flipped separators, and a lookup miss left the file with
                # both markers None. That reads downstream as "no marker at
                # all", so a file whose provenance is perfectly recorded was
                # refused with "written by an older version" — a reason that
                # sends the user looking for the wrong problem.
                key = os.path.normcase(str(Path(src)))
                if key in index:
                    markers[index[key]] = info
                else:
                    logger.warning(f"Provenance: marker read came back for a path "
                                   f"this run did not ask about, so it cannot be "
                                   f"matched | {src}")
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


def _markers_match(out_info: dict, src_info: dict) -> bool:
    """Does the existing output record the SAME origin as the source JXL in
    front of it?

    Both sides carry the encoder's jxlphoto-src/srcsum pair pointing at the
    ORIGINAL source (the TIFF), so equality means "same photo": the recompressor
    copies them verbatim, and an earlier recompress of this same source stamped
    the same ids. Either id matching is enough — src is the location, srcsum
    the bytes; both None on either side proves nothing (fail closed: False).
    """
    if out_info.get("srcsum") and src_info.get("srcsum"):
        if out_info["srcsum"] == src_info["srcsum"]:
            return True
    if out_info.get("src") and src_info.get("src"):
        if out_info["src"] == src_info["src"]:
            return True
    return False


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


# ---------------------------------------------------------------------------
# Encoding-parameter record: read the "cjxl d=X e=Y" tag the encoder stamps,
# classify the requested recompression against it, and restamp after encoding.
# ---------------------------------------------------------------------------

def _parse_encode_params(text: str):
    """(distance, effort) from a metadata string, or None.

    The chain is append-only (" | "-joined), so the LAST match in the string
    is the current file's parameters. Only MACHINE-BLOCK segments count: a
    caption merely containing "cjxl d=1 e=7" is user text, not an encode
    record.
    """
    found = []
    for block in _MACHINE_BLOCK_RE.findall(str(text)):
        found.extend(_ENCODE_TAG_RE.findall(block))
    if not found:
        return None
    d, e = found[-1]
    try:
        return float(d), int(e)
    except (TypeError, ValueError):
        return None


def _strip_encode_params(text: str):
    """(cleaned_text, orphans) with the machine block removed.

    Removes the gen= token and the WHOLE cjxl d=/e= chain (one unit) from a
    " | "-joined metadata string, keeping any unrelated text (an original
    caption, a real CreatorTool). Orphaned gen= segments — left behind by a
    corrupted or hand-edited field — are removed ONLY as whole segments and
    ONLY at the tail (the machine-block region): "Project gen=3 phase 2" and
    "gen=2 | My caption" are user text and stay. `orphans` counts how many
    were stripped so the caller can log it: an orphan means something wrote
    the field badly earlier.
    """
    cleaned = _MACHINE_BLOCK_RE.sub("", str(text))
    parts = [p.strip() for p in cleaned.split("|")]
    parts = [p for p in parts if p]
    orphans = 0
    while parts and re.fullmatch(r"gen=\d+", parts[-1]):
        parts.pop()
        orphans += 1
    return " | ".join(parts), orphans


def _reconcile_gen(text: str):
    """(gen, stored, counted) for the field — gen is DERIVED, never incremented.

    counted = number of "cjxl d=X" chain entries with X > 0 (a d=0 lossless
    entry is appended to the chain but costs no quality, so it is not a
    generation). stored = the gen= token, 0 when absent or unparseable.
    gen = max(stored, counted): if chain entries were removed the stored gen
    is the better number, if entries were added without updating gen the
    count is better — undercounting generations is the error that causes
    damage, so the larger value wins in both directions. A malformed chain
    entry never stops the rest from being counted. Legacy fields (chain, no
    gen=) need no special case: max(0, counted) == counted.

    Only MACHINE-BLOCK segments count: a caption merely containing
    "cjxl d=1 e=7" is user text, not an encode record, and must not raise the
    generation count.
    """
    s = str(text)
    m = _GEN_TAG_RE.search(s)
    stored = int(m.group(1)) if m else 0
    counted = 0
    for block in _MACHINE_BLOCK_RE.findall(s):
        for d, _e in _ENCODE_TAG_RE.findall(block):
            try:
                if float(d) > 0:
                    counted += 1
            except ValueError:
                continue  # malformed entry (e.g. d=1.2.3): skip, keep counting
    return max(stored, counted), stored, counted


def _append_encode_entry(text: str, new_d, new_e):
    """(new_text, stored, counted_before, orphans): append "cjxl d=new_d
    e=new_e" to the field's chain and rewrite the gen= token at the head of
    the machine block.

    Append-only is the design: the chain is the lineage history, and
    replacing it would erase the generation count that --on-regeneration
    guards. The user's own text (a caption) stays FIRST — dc:Description is
    visible in Windows Properties and machine output must not push a caption
    behind it. gen is reconciled over the final chain (never incremented):
    a wrong value self-corrects on the next pass. `stored`/`counted_before`
    are returned so the caller can log a divergence (once per run); `orphans`
    comes from _strip_encode_params.

    Chain entries are read back from the MACHINE BLOCKS only: a caption that
    merely contains "cjxl d=1 e=7" is user text and is not absorbed into the
    chain (and not counted as a generation).
    """
    s = str(text)
    _gen_old, stored, counted = _reconcile_gen(s)
    user, orphans = _strip_encode_params(s)
    entries = []
    for block in _MACHINE_BLOCK_RE.findall(s):
        entries.extend(f"cjxl d={d} e={e}"
                       for d, e in _ENCODE_TAG_RE.findall(block))
    entries.append(f"cjxl d={new_d} e={new_e}")
    counted_new = counted
    try:
        if float(new_d) > 0:
            counted_new += 1
    except (TypeError, ValueError):
        pass
    gen = max(stored, counted_new)
    block = " | ".join([f"gen={gen}"] + entries)
    return (f"{user} | {block}" if user else block), stored, counted, orphans


def _merge_lineage_blocks(desc: str, software: str):
    """Merge the two fields' machine blocks: (entries, stored_gen).

    The record can be SPLIT across dc:Description and Software (the user
    switched --encode-tag, a file came through a tool that keeps only one
    field, or an older version left a shadow copy behind). Whichever side
    holds it, the chain must survive the restamp.

    Entries are NEVER deduplicated inside one field: "cjxl d=0.1 e=7 | cjxl
    d=0.1 e=7" is two real generations (decode, then re-encode at the same
    settings — exactly what the append-only chain exists to record), and
    collapsing them undercounts gen in the damaging direction. Across the two
    fields:
      * the same chain in both (a mirror), or one a prefix of the other (a
        copy that was later extended), is ONE history -> the longer one;
      * anything else is split history -> both, dc:Description first (the
        older side in every case this toolkit produces: a decoder-made TIFF
        carries the JXL's chain there, and the new encode lands in Software).
    stored gen = max of both gen= tokens. Entries inside a caption's running
    text never match: the block regex only matches whole " | "-delimited
    segments.
    """
    chains = []
    for text in (desc, software):
        chain = []
        for block in _MACHINE_BLOCK_RE.findall(str(text)):
            chain.extend(_ENCODE_TAG_RE.findall(block))
        chains.append(chain)
    a, b = chains
    if not a or b[:len(a)] == a:
        entries = list(b) if len(b) >= len(a) else list(a)
    elif not b or a[:len(b)] == b:
        entries = list(a)
    else:
        entries = a + b
    stored = 0
    for text in (desc, software):
        m = _GEN_TAG_RE.search(str(text))
        if m:
            stored = max(stored, int(m.group(1)))
    return entries, stored


def _read_encode_params_batch(paths: list) -> dict:
    """{path str: {'desc': str, 'software': str, 'params': (d,e)|None,
    'gen': int}} with one exiftool call per 400 files — per-file spawns were
    minutes on a library.

    `gen` is the reconciled generation count (max of the stored gen= token
    and the lossy chain length), read from the field that carries the record.
    Files exiftool cannot read come back with empty strings and params None,
    which classifies as 'unknown' — the ON_UNKNOWN policy decides.
    """
    info = {str(p): {"desc": "", "software": "", "params": None, "gen": 0}
            for p in paths}
    index = {os.path.normcase(str(p)): str(p) for p in paths}
    if not paths:
        return info
    batch_lines = ["-j", "-s", "-s", "-s", "-XMP-dc:Description", "-Software",
                   "-charset", "FileName=UTF8", "-charset", "UTF8"]
    BATCH = 400
    for i in range(0, len(paths), BATCH):
        chunk = paths[i:i + BATCH]
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
                logger.warning(f"Encode-tag read failed for a batch of "
                               f"{len(chunk)} file(s) (rc={r.returncode}) — "
                               f"treating them as unknown origin")
                continue
            data = json.loads(r.stdout)
            for entry in data:
                src = entry.get("SourceFile")
                if src is None:
                    continue
                key = os.path.normcase(str(Path(src)))
                if key not in index:
                    continue
                desc = str(entry.get("Description") or "")
                software = str(entry.get("Software") or "")
                # The record can be SPLIT across the two fields (an older
                # version moved it, or left a shadow copy). Read the UNION:
                # the current parameters are the last entry of the merged
                # chain, and the generation count reconciles over BOTH —
                # counting one side alone undercounts in exactly the
                # damaging direction.
                merged_entries, merged_stored = _merge_lineage_blocks(
                    desc, software)
                params = None
                if merged_entries:
                    d, e = merged_entries[-1]
                    try:
                        params = (float(d), int(e))
                    except (TypeError, ValueError):
                        params = None
                counted = 0
                for d, _e in merged_entries:
                    try:
                        if float(d) > 0:
                            counted += 1
                    except ValueError:
                        continue
                gen = max(merged_stored, counted) if (merged_entries
                                                      or merged_stored) else 0
                info[index[key]] = {"desc": desc, "software": software,
                                    "params": params, "gen": gen}
        except Exception as e:
            logger.warning(f"Encode-tag batch failed ({e}); {len(chunk)} file(s) "
                           f"treated as unknown origin")
        finally:
            if argfile:
                try:
                    os.unlink(argfile)
                except OSError:
                    pass
    return info


def _classify(src_params, new_d: float, new_e: int, gen: int = 0):
    """(category, reason) for recompressing a file whose recorded parameters
    are `src_params` ((d, e) or None) to the requested (new_d, new_e).

    `gen` is the reconciled generation count: it matters when the LAST chain
    entry is a lossless d=0 pass — the file LOOKS lossless but already
    carries lossy generations, and calling this "the FIRST lossy generation"
    would misdescribe exactly the case --on-regeneration guards.

    Categories:
      "ok"        — the request makes sense (smaller target, or first lossy
                    generation from a lossless source)
      "downgrade" — it cannot gain anything: same distance as an already-lossy
                    source, or a LOWER distance (quality cannot be recovered)
      "unknown"   — the file carries no encoding record
    """
    if src_params is None:
        return ("unknown", "no cjxl d=/e= record found (not written by this "
                           "toolkit, or written with --encode-tag off)")
    d_old, e_old = src_params
    if d_old == 0:
        if new_d == 0:
            if new_e > e_old:
                return ("ok", f"lossless source; effort {e_old}->{new_e} "
                              f"shrinks the file with zero quality loss")
            return ("downgrade", f"source is lossless (d=0 e={e_old}) and the "
                                 f"request is also lossless with effort {new_e} <= {e_old}: "
                                 f"same pixels, no size gain to buy")
        if gen >= 1:
            return ("ok", f"the last recorded pass was lossless (d=0), but the "
                          f"file already carries {gen} lossy generation(s) "
                          f"(gen={gen}): this is ANOTHER one at d={new_d}, not "
                          f"the first")
        return ("ok", f"source is lossless (d=0): this is the FIRST lossy "
                      f"generation, best possible quality at d={new_d}")
    if new_d > d_old:
        return ("ok", f"smaller target: d={d_old} -> d={new_d} "
                      f"(one generation of lossy re-encode)")
    if new_d == d_old:
        if new_e < e_old:
            return ("downgrade", f"same distance d={d_old} with LOWER effort "
                                 f"{new_e} < {e_old}: bigger file, same quality, "
                                 f"plus a generation of loss")
        return ("downgrade", f"same distance d={d_old}: re-encoding buys nothing "
                             f"but a generation of loss (effort {e_old}->{new_e} "
                             f"only changes compute time here)")
    return ("downgrade", f"source is already lossy at d={d_old}; d={new_d} is a "
                         f"HIGHER quality it cannot recover — the file only grows")


def _policy_action(category: str, jbrd: bool) -> str:
    """Resolve the configured policies to an action: convert/copy/skip/ask."""
    if jbrd and JBRD_POLICY != "convert":
        return JBRD_POLICY
    if category == "ok":
        return "convert"
    if category == "downgrade":
        return ON_DOWNGRADE
    return ON_UNKNOWN


_ACTION_RANK = {"convert": 0, "ask": 1, "copy": 2, "skip": 3}


def _more_conservative(a: str, b: str) -> str:
    """The safer of two policy actions: skip > copy > ask > convert."""
    return a if _ACTION_RANK[a] >= _ACTION_RANK[b] else b


def _regeneration_action(gen: int, new_d: float):
    """ON_REGENERATION when this request would add a REPEATED lossy generation
    to a file that has already been lossy-recompressed at least once; None
    when the guard does not apply.

    The threshold is gen >= 2, not >= 1: every lossy file the toolkit's own
    encoder produces is born at gen=1, so guarding at 1 turned the
    recompressor's MAIN use case — taking the encoder's d=0.1 preview to the
    final d=1.0 — into an "ask" that silently skipped everything on
    headless runs. The first recompression of an encoder output is a normal,
    expected operation; the guard exists for the SECOND lossy re-encode
    onwards, where each pass costs ~1 dB regardless of step size.

    A lossless request (new_d == 0) adds no generation — the d=0 entry is
    appended to the chain but costs no quality, so the guard stays quiet and
    --on-downgrade keeps covering the lossless-on-lossless cases.
    """
    if gen >= 2 and new_d > 0:
        return ON_REGENERATION
    return None


_gen_divergence_logged = False


def _log_gen_notes_once(stored: int, counted: int) -> None:
    """Divergence between the stored gen= and the chain length is information,
    not an error (max() already kept the safer value) — log it once per run,
    not per file. Legacy files (no gen= token, stored == 0) are the normal
    case and never count as divergence.
    """
    global _gen_divergence_logged
    if stored and stored != counted and not _gen_divergence_logged:
        _gen_divergence_logged = True
        logger.info("At least one file's stored gen= disagrees with its cjxl "
                    "chain length — kept the larger (safer) value; the field "
                    "was corrected on the restamp.")


def _restamp_args(desc: str, software: str, label: str = "") -> list:
    """exiftool argfile lines that APPEND the new encoding parameters to the
    chain, wherever ENCODE_TAG_MODE says — and strip the record from the other
    field, so no stale d=/e= survives to mislead a later run.

    The chain is append-only (the encoder already concatenates): replacing it
    would erase the generation history that --on-regeneration guards.
    `label` (a file name) only prefixes the orphan-strip log lines.

    The chain that survives a field CHANGE is the UNION of both fields: if
    the record lived in Software and this restamp writes dc:Description (or
    the other way around), merging keeps every past entry and the stored
    gen= — dropping the old field's chain silently undercounts generations
    in the damaging direction.
    """
    lines = []
    merged_entries, merged_stored = _merge_lineage_blocks(desc, software)
    merged_chain = " | ".join(
        ([f"gen={merged_stored}"] if merged_stored else [])
        + [f"cjxl d={d} e={e}" for d, e in merged_entries])

    def _seed(user_text: str) -> str:
        user, _o = _strip_encode_params(user_text)
        if user and merged_chain:
            return f"{user} | {merged_chain}"
        return user or merged_chain

    if ENCODE_TAG_MODE == "xmp":
        new_desc, stored, counted, orphans = _append_encode_entry(
            _seed(desc), CJXL_DISTANCE, CJXL_EFFORT)
        _log_gen_notes_once(stored, counted)
        if orphans:
            logger.warning(f"Stripped {orphans} orphaned gen= token(s) from "
                           f"dc:Description of {label or 'a file'} — the field "
                           f"was written badly earlier")
        lines.append("-XMP-dc:Description=" + _argfile_safe(new_desc))
        clean_sw, sw_orphans = _strip_encode_params(software)
        if sw_orphans:
            logger.warning(f"Stripped {sw_orphans} orphaned gen= token(s) from "
                           f"Software of {label or 'a file'}")
        if clean_sw != software:
            lines.append("-Software=" + _argfile_safe(clean_sw))
    elif ENCODE_TAG_MODE == "software":
        new_sw, stored, counted, orphans = _append_encode_entry(
            _seed(software), CJXL_DISTANCE, CJXL_EFFORT)
        _log_gen_notes_once(stored, counted)
        if orphans:
            logger.warning(f"Stripped {orphans} orphaned gen= token(s) from "
                           f"Software of {label or 'a file'} — the field was "
                           f"written badly earlier")
        lines.append("-Software=" + _argfile_safe(new_sw))
        clean_desc, d_orphans = _strip_encode_params(desc)
        if d_orphans:
            logger.warning(f"Stripped {d_orphans} orphaned gen= token(s) from "
                           f"dc:Description of {label or 'a file'}")
        if clean_desc != desc:
            lines.append("-XMP-dc:Description=" + _argfile_safe(clean_desc))
    else:  # off: record nothing, and leave no stale record behind — gen and
        # chain go together. This is the only way to deliberately discard
        # the lineage.
        clean_desc, d_orphans = _strip_encode_params(desc)
        clean_sw, s_orphans = _strip_encode_params(software)
        for field, n in (("dc:Description", d_orphans), ("Software", s_orphans)):
            if n:
                logger.warning(f"Stripped {n} orphaned gen= token(s) from "
                               f"{field} of {label or 'a file'}")
        if clean_desc != desc:
            lines.append("-XMP-dc:Description=" + _argfile_safe(clean_desc))
        if clean_sw != software:
            lines.append("-Software=" + _argfile_safe(clean_sw))
    return lines


# ---------------------------------------------------------------------------
# Round-trip verification (decode both sides, compare)
# ---------------------------------------------------------------------------

_VERIFY_CHUNK_ROWS = 512


def _canon_for_compare(a):
    """Both sides of the comparison in one shape/dtype convention.

    The encoder feeds cjxl a 3D array (2D pages get a trailing axis) and
    promotes 8-bit to 16-bit with *257; djxl gives back 2D for a grayscale JXL.
    Squeeze the single-channel axis away on both so a grayscale page compares
    as (H, W) either way.
    """
    if a.ndim == 3 and a.shape[2] == 1:
        a = a[:, :, 0]
    if a.dtype == np.uint8:
        return a.astype(np.uint16) * 257
    if a.dtype != np.uint16:
        return a.astype(np.uint16)
    return a


def _decode_jxl_for_verify(jxl_path: Path, tmp_dir: Path):
    """Decode a JXL back to pixels. Raises on any failure — a verification that
    cannot run must never read as a verification that passed."""
    png_path = tmp_dir / "verify.png"
    r = subprocess.run(["djxl", str(jxl_path), str(png_path)],
                       capture_output=True, timeout=CJXL_TIMEOUT)
    if r.returncode != 0 or not png_path.exists():
        raise RuntimeError(f"djxl: {(r.stderr or b'').decode(errors='replace')[:200]}")
    # imagecodecs, never PIL: PIL silently quantises 16-bit RGB/RGBA PNGs to
    # 8-bit, which would make every lossless comparison fail for a reason that
    # has nothing to do with the encode. Checked up front in main().
    import imagecodecs
    return imagecodecs.png_decode(png_path.read_bytes())


def _compare_stats(src, dec):
    """(source mean, decoded mean, MSE), accumulated over row blocks."""
    total = src.size
    s_sum = d_sum = sq = 0.0
    for y in range(0, src.shape[0], _VERIFY_CHUNK_ROWS):
        a = src[y:y + _VERIFY_CHUNK_ROWS].astype(np.float64)
        b = dec[y:y + _VERIFY_CHUNK_ROWS].astype(np.float64)
        s_sum += float(a.sum())
        d_sum += float(b.sum())
        diff = a - b
        sq += float((diff * diff).sum())
    return s_sum / total, d_sum / total, sq / total


def _verify_roundtrip_jxl(src_jxl: Path, out_jxl: Path, src_distance,
                          new_distance: float):
    """Does `out_jxl` really hold what `src_jxl` held?

    Pixel-identical only when BOTH ends are lossless (a lossy source re-encoded
    at d=0 goes through the lossless path of the decode it already was, and
    small container-level differences are legitimate). Anything involving a
    lossy target is a brightness + PSNR sanity check: it is there to catch a
    black or scrambled encode, not to grade the compression. Any failure to
    even run the check returns False: this gates an irreversible delete, so
    "could not tell" must mean "keep".
    """
    import numpy as np
    try:
        with tempfile.TemporaryDirectory(prefix="jxl_verify_src_", dir=TEMP_DIR) as tmp1:
            src = _decode_jxl_for_verify(src_jxl, Path(tmp1))
        with tempfile.TemporaryDirectory(prefix="jxl_verify_out_", dir=TEMP_DIR) as tmp2:
            dec = _decode_jxl_for_verify(out_jxl, Path(tmp2))
    except Exception as e:
        return False, f"could not verify: {e}"

    src, dec = _canon_for_compare(src), _canon_for_compare(dec)
    if src.shape != dec.shape:
        return False, f"shape mismatch: source {src.shape} vs decoded {dec.shape}"

    if src_distance == 0 and new_distance == 0:
        # Lossless means lossless. No tolerance, no statistics.
        if np.array_equal(src, dec):
            return True, "pixel-identical"
        return False, "lossless re-encode did not decode back pixel-identical"

    s_mean, d_mean, mse = _compare_stats(src, dec)
    ratio = (d_mean / s_mean) if s_mean > 0 else (1.0 if d_mean == 0 else 0.0)
    psnr = float("inf") if mse <= 0 else 10.0 * math.log10((65535.0 ** 2) / mse)
    # Bounded on BOTH sides: a decode that blows out is just as wrong as one
    # that comes back dark.
    lo = VERIFY_LOSSY_MIN_MEAN_RATIO
    hi = 1.0 / lo if lo > 0 else float("inf")
    detail = f"mean ratio {ratio:.3f}, PSNR {psnr:.1f} dB"
    if not (lo <= ratio <= hi):
        return False, f"{detail} — brightness is not the source's"
    if psnr < VERIFY_LOSSY_MIN_PSNR:
        return False, f"{detail} — below the {VERIFY_LOSSY_MIN_PSNR} dB floor"
    return True, detail


def _would_skip(jxl_path: Path, final_path: Path) -> bool:
    """Would convert_one report SKIP for this pair?

    Mirrors the decision at the top of convert_one exactly — including its
    TOCTOU fallback, where an unreadable stat means "stale, convert it". Used
    only by the dry-run preview of --delete-skipped: a destructive option that
    could not be previewed would be the wrong kind of opt-in.
    """
    if not final_path.exists():
        return False
    if OVERWRITE is False:
        return True
    if OVERWRITE == "smart":
        try:
            return jxl_path.stat().st_mtime <= final_path.stat().st_mtime
        except OSError:
            return False
    return False        # OVERWRITE True: always reconvert, never a skip


# ---------------------------------------------------------------------------
# Output path resolution (modes 0-8). Modes 0/1 are resolved in main().
# ---------------------------------------------------------------------------

def resolve_output(jxl_path: Path, mode: int, input_root: Path):
    # Mode 0: single file in-place — handled in main() before calling this
    # Mode 1: single file -> recompressed_jxl/ subfolder — handled in main()

    def _warn_if_outside(result: Path) -> Path:
        # Modes 4/5 can land OUTSIDE the selected input tree for files at its
        # root — surface that once per file instead of surprising the user
        # later. Modes 3/4/5 accept a single FILE as input_root; anchor at the
        # file's parent's parent so a legitimate sibling output is not flagged.
        anchor = input_root.parent.parent if input_root.is_file() else input_root
        if result is not None and not _is_relative_to(result, anchor):
            logger.warning(f"Output outside input tree: {jxl_path.name} -> {result}")
        return result

    if mode == 2:
        # Flat directory: input_root/photo.jxl
        return input_root / jxl_path.name

    elif mode == 3:
        # Subfolder inside each JXL folder
        return jxl_path.parent / JXL_FOLDER_NAME / jxl_path.name

    elif mode == 4:
        # Rename folder replacing JXL token with JXL_SUFFIX_REPLACE
        old_name = jxl_path.parent.name
        new_name = _replace_suffix_token(old_name, JXL_SUFFIX_TO_REPLACE, JXL_SUFFIX_REPLACE)
        if new_name == old_name:
            new_name = old_name + "_" + JXL_SUFFIX_REPLACE
            logger.warning(f"'{JXL_SUFFIX_TO_REPLACE}' not found as a token in '{old_name}', using '{new_name}'")
        return _warn_if_outside(jxl_path.parent.parent / new_name / jxl_path.name)

    elif mode == 5:
        # Sibling folder next to each JXL folder
        return _warn_if_outside(jxl_path.parent.parent / JXL_FOLDER_NAME / jxl_path.name)

    elif mode == 6:
        # EXPORT_MARKER anchor — only JXLs INSIDE export marker folder
        # Match only path *directory* parts (parts[:-1]); a JXL whose own
        # filename happens to start/end with the marker is not an anchor.
        parts = jxl_path.parts
        marker_lower = EXPORT_MARKER.lower()
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
        return export_dir / EXPORT_JXL_FOLDER / rel

    elif mode == 7:
        # EXPORT_MARKER anchor — only JXLs inside export marker/[subfolder]
        parts = jxl_path.parts
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
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])

        return export_dir / EXPORT_JXL_FOLDER / rel

    elif mode == 8:
        # In-place recursive: the output REPLACES the source JXL.
        return jxl_path

    raise ValueError(f"Invalid mode: {mode}")


# ---------------------------------------------------------------------------
# File discovery. Inputs and outputs are BOTH .jxl here, so every recursive
# scan skips this tool's own output folder names — otherwise mode 8/2 would
# re-recompress its own output on the next run.
# ---------------------------------------------------------------------------

JXL_EXTS = frozenset({".jxl"})

_RECOMPRESSOR_OUTPUT_FOLDERS = frozenset(
    {"recompressed_jxl", "jxl_recompressed", "jxl_small", "16b_jxl_small"})


def _is_own_output_path(parts_lower) -> bool:
    """True if any directory part is one of this tool's output folder names."""
    return any(p in _RECOMPRESSOR_OUTPUT_FOLDERS for p in parts_lower)


def _iter_jxls(paths, root=None):
    seen = set()
    files = []
    _scan = _scan_state(root)
    for f in paths:
        _scan_tick(_scan, len(files))
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
    _scan_done(_scan, len(files))
    return sorted(files)


def find_files_mode0(input_path: Path):
    return _iter_jxls(input_path.glob("*"), input_path)


def find_jxls_recursive(input_path: Path):
    raw = _iter_jxls(input_path.rglob("*"), input_path)
    # Skip this tool's own output folders BELOW the input root — but never the
    # root itself: pointing the run AT a "recompressed_jxl" folder to compress
    # it again is a legitimate use, and the files there are its direct content.
    filtered = []
    for f in raw:
        try:
            below = [p.lower() for p in f.relative_to(input_path).parts[:-1]]
        except ValueError:
            below = [p.lower() for p in f.parts[:-1]]
        if not _is_own_output_path(below):
            filtered.append(f)
    skipped = len(raw) - len(filtered)
    if skipped:
        logger.info(f"Ignored {skipped} JXL(s) inside recompressor output folders "
                    f"({', '.join(sorted(_RECOMPRESSOR_OUTPUT_FOLDERS))}) — "
                    f"those are this tool's own outputs")
    return filtered


def find_jxls_mode6(input_path: Path):
    """Mode 6: only JXLs inside folders containing EXPORT_MARKER (any subfolder)."""
    all_jxls = find_jxls_recursive(input_path)
    filtered = []
    marker_lower = EXPORT_MARKER.lower()
    for t in all_jxls:
        # Match only directory parts; the filename itself is not an anchor
        parts_str = list(t.parts[:-1])
        export_idx = next((i for i, p in enumerate(parts_str)
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is not None:
            filtered.append(t)
    return sorted(filtered)


def find_jxls_mode7(input_path: Path):
    """Mode 7: only JXLs inside EXPORT_MARKER/EXPORT_JXL_SUBFOLDER."""
    all_jxls = find_jxls_recursive(input_path)
    filtered = []
    marker_lower = EXPORT_MARKER.lower()
    subfolder_lower = EXPORT_JXL_SUBFOLDER.lower()
    for t in all_jxls:
        # Match only directory parts; the filename itself is not an anchor
        parts_str = list(t.parts[:-1])
        export_idx = next((i for i, p in enumerate(parts_str)
                           if _marker_matches(p.lower(), marker_lower)), None)
        if export_idx is None:
            continue
        if EXPORT_JXL_SUBFOLDER:
            if export_idx + 1 < len(parts_str) and parts_str[export_idx + 1].lower() == subfolder_lower:
                filtered.append(t)
        else:
            filtered.append(t)
    return sorted(filtered)


# ---------------------------------------------------------------------------
# Per-file conversion
# ---------------------------------------------------------------------------

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
            try:
                h = real_size.to_bytes(4, "big") + h[4:8]
            except OverflowError:
                # A file just under 4 GiB whose trailing size-0 box spans most
                # of it computes a real_size that no longer fits the 32-bit
                # field — report it like the truncation guards above, not as
                # an uncaught traceback.
                raise RuntimeError(
                    f"Cannot re-header size-0 box {name!r}: real size {real_size} "
                    f"exceeds the 32-bit box size field") from None
        out += h + p
    jxl_path.write_bytes(out)


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


def convert_one(jxl_path: Path, write_path: Path, final_path: Path,
                action: str, in_place: bool, desc: str, software: str,
                src_distance):
    """Recompress (or verbatim-copy) one JXL.

    action: "convert" (cjxl re-encode + metadata restamp) or "copy" (verbatim
    bytes — the downgrade/jbrd policy answer: zero generation loss).

    Returns (str(jxl_path), status, str(final_path)) with status in
    ok/copied/overwrite/skipped/aborted/error. The in-place replace itself is
    done by process_group after this returns — here we only build and verify
    the replacement at write_path.
    """
    n, total = next_count()
    # Initialized BEFORE any statement that can raise so the except handler
    # always has them (same pattern as the encoder and the transcoder): a
    # failed conversion must never leave its partial output behind — without
    # staging it sits at the FINAL path, where the next sync run would treat
    # it as a finished archive and skip the file forever; in place it sits
    # next to the source as <uuid>_name.jxl, where the next run picks it up
    # as a NEW input.
    output_dirty = False
    _pre_identity = _capture_output_identity(write_path, final_path)
    try:
        if _aborted():
            return (str(jxl_path), "aborted", str(final_path))

        if not in_place and _would_skip(jxl_path, final_path):
            logger.info(f"[{n}/{total}] SKIP (exists) | {jxl_path.name}")
            return (str(jxl_path), "skipped", str(final_path))

        # Captured BEFORE any write: without staging the output IS written at
        # final_path, so checking existence afterwards would always read True.
        existed_before = final_path.exists()

        if action == "copy":
            if in_place:
                logger.info(f"[{n}/{total}] SKIP (policy copy: already in place) | {jxl_path.name}")
                return (str(jxl_path), "skipped", str(final_path))
            write_path.parent.mkdir(parents=True, exist_ok=True)
            output_dirty = True
            shutil.copy2(str(jxl_path), str(write_path))
            if not _verify_jxl_integrity(write_path):
                raise RuntimeError("copied file failed the JXL integrity check")
            logger.info(f"[{n}/{total}] COPY | {jxl_path.name} -> {final_path.name}")
            return (str(jxl_path), "copied", str(final_path))

        # action == "convert"
        write_path.parent.mkdir(parents=True, exist_ok=True)
        output_dirty = True
        container_flag = ["--container=1"] if CJXL_DISTANCE > 0 else []
        cjxl_cmd = ([_get_cjxl_cmd() or "cjxl", str(jxl_path), str(write_path),
                     "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT)]
                    + container_flag + _cjxl_buffering_flag())
        r = subprocess.run(cjxl_cmd, capture_output=True, timeout=CJXL_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {(r.stderr or b'').decode(errors='replace')[:200]}")

        # Metadata: everything the source JXL carries (EXIF, XMP, the base64 ICC
        # in CreatorTool, jxlphoto-* provenance and multi-page group markers)
        # copied verbatim — then the encoding tag restamped to the NEW d/e.
        #
        # -api Compress=0: cjxl >= 0.12 carries the source's Exif/XMP across
        # itself, as Brotli-compressed "brob" boxes, and exiftool edits them in
        # that form. IrfanView cannot read brob (README: "Viewer quirks"), so
        # without this the recompressed archive lost its visible EXIF even
        # with the boxes reordered — the encoder's outputs use plain boxes.
        r2 = _run_exiftool_argfile(
            ["-overwrite_original", "-api", "Compress=0",
             "-tagsfromfile", str(jxl_path),
             "-exif:all", "-xmp:all", "-iptc:all"]
            + _restamp_args(desc, software, label=jxl_path.name)
            + [str(write_path)], timeout=120)
        if r2.returncode != 0:
            # A failed metadata copy is an ERROR, not a warning: the output
            # would silently miss the ICC/EXIF this whole tool exists to keep.
            raise RuntimeError(f"exiftool metadata copy: {(r2.stderr or '')[:200]}")

        # exiftool re-appends its metadata boxes AFTER the codestream, which
        # hides the Exif/XMP from viewers that only scan the boxes before it
        # (IrfanView — named in the README as supported). Reorder BEFORE the
        # integrity check so the verified bytes are the final on-disk bytes.
        reorder_jxl_boxes(write_path)

        if not _verify_jxl_integrity(write_path):
            raise RuntimeError("output failed the JXL integrity check")

        status = "ok"
        if KEEP_SMALLER:
            try:
                if write_path.stat().st_size >= jxl_path.stat().st_size:
                    # The re-encode LOST to the existing compression: keep the
                    # source bytes instead (zero generation loss, smaller file).
                    if in_place:
                        # Nothing to replace: the original is already the best
                        # file. Drop the re-encode and report a skip.
                        try:
                            write_path.unlink()
                        except OSError:
                            pass
                        logger.info(f"[{n}/{total}] SKIP (not smaller, original kept) | {jxl_path.name}")
                        return (str(jxl_path), "skipped", str(final_path))
                    shutil.copy2(str(jxl_path), str(write_path))
                    if not _verify_jxl_integrity(write_path):
                        raise RuntimeError("kept-smaller copy failed the JXL integrity check")
                    status = "copied"
                    logger.info(f"[{n}/{total}] COPY (not smaller) | {jxl_path.name}")
            except OSError:
                pass

        if status == "ok" and VERIFY_ROUNDTRIP:
            ok, detail = _verify_roundtrip_jxl(jxl_path, write_path,
                                               src_distance, CJXL_DISTANCE)
            if not ok:
                raise RuntimeError(f"round-trip verification failed: {detail}")
            logger.debug(f" >Round-trip verified ({detail})")

        overwritten = existed_before and not in_place
        label = ("RECOMPRESS" if status == "ok" else "COPY")
        logger.info(f"[{n}/{total}] {label} | {jxl_path.name} -> {final_path.name}")
        return (str(jxl_path), "overwrite" if overwritten and status == "ok" else status,
                str(final_path))

    except subprocess.TimeoutExpired:
        if output_dirty:
            _delete_partial_if_written(write_path, final_path, _pre_identity)
        _error_details[str(jxl_path)] = f"codec timed out after {CJXL_TIMEOUT}s"
        logger.error(f"[{n}/{total}] TIMEOUT | {jxl_path.name}")
        return (str(jxl_path), "error", str(final_path))
    except Exception as e:
        if output_dirty:
            _delete_partial_if_written(write_path, final_path, _pre_identity)
        _error_details[str(jxl_path)] = str(e)
        logger.error(f"[{n}/{total}] ERROR | {jxl_path.name} | {e}")
        _abort_if_disk_full(write_path.parent if write_path is not None else jxl_path.parent,
                            jxl_path.stat().st_size if jxl_path.exists() else 0)
        return (str(jxl_path), "error", str(final_path))


def process_group(items, workers: int):
    """Convert/copy all planned items in parallel, promote out of staging (or
    replace in place), then run the delete gate. `items` is a list of dicts
    with keys: src, final, write, action, in_place, desc, software, src_d.
    Returns (results, promoted) — results: {src: (status, final)},
    promoted: set of src strs whose output reached its final path THIS run.
    """
    results = {}
    promoted = set()
    staging_used = TEMP2_DIR is not None

    def _submit(ex, it):
        return ex.submit(convert_one, it["src"], it["write"], it["final"],
                         it["action"], it["in_place"], it["desc"],
                         it["software"], it["src_d"])

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {_submit(ex, it): it for it in items}
        for fut in as_completed(futures):
            it = futures[fut]
            # One crashing future must not take the whole batch down: record
            # the item as an error and keep settling the rest.
            try:
                src_str, status, final_str = fut.result()
            except Exception as e:
                src_str, final_str = str(it["src"]), str(it["final"])
                status = "error"
                _error_details[src_str] = f"worker crashed: {e}"
                logger.error(f"  WORKER CRASHED | {it['src'].name} | {e}")
            results[src_str] = (status, final_str)
            if status not in ("ok", "overwrite", "copied"):
                continue
            try:
                final_path = Path(final_str)
                if it["write"] == final_path:
                    # No staging and not in place: written directly at the final path.
                    promoted.add(src_str)
                    continue
                if it["in_place"]:
                    # The original is replaced ONLY now — after every gate above
                    # passed on the verified write_path. Same-volume os.replace is
                    # atomic; staging goes through a temp file in the DESTINATION
                    # folder first.
                    #
                    # Why not _promote_from_staging here: in place, the
                    # destination file IS the only copy there is. A cross-volume
                    # shutil.move copies ONTO it non-atomically — a failure
                    # half-way left the original destroyed and the sole good copy
                    # in staging under a UUID name, which --clean-staging sweeps
                    # an hour later. Moving to a temp file in the destination
                    # folder keeps the original intact until a same-volume
                    # os.replace swaps it atomically.
                    if staging_used:
                        dest_tmp = (final_path.parent
                                    / f"{uuid.uuid4().hex}_{final_path.name}")
                        try:
                            shutil.move(str(it["write"]), str(dest_tmp))
                        except OSError as e:
                            logger.error(f"  REPLACE FAILED, original kept | "
                                         f"{final_path.name} | {e}")
                            try:
                                if dest_tmp.exists():
                                    dest_tmp.unlink()
                            except OSError:
                                pass
                            moved = False
                        else:
                            try:
                                os.replace(str(dest_tmp), str(final_path))
                                moved = True
                            except OSError as e:
                                # The complete new file survives as dest_tmp; the
                                # original is untouched. Leave the temp file in
                                # place (deleting it would destroy the only good
                                # copy) and say exactly where it is.
                                logger.error(f"  REPLACE FAILED, original kept; the "
                                             f"complete re-encode is at {dest_tmp} "
                                             f"(rename it over the original once the "
                                             f"problem is fixed) | {final_path.name} | {e}")
                                moved = False
                    else:
                        try:
                            os.replace(str(it["write"]), str(final_path))
                            moved = True
                        except OSError as e:
                            logger.error(f"  REPLACE FAILED, original kept | {final_path.name} | {e}")
                            moved = False
                    if not moved:
                        results[src_str] = ("error", final_str)
                        continue
                    if not _verify_jxl_integrity(final_path):
                        logger.error(f"  Replaced file failed the final integrity check | {final_path.name}")
                        results[src_str] = ("error", final_str)
                        continue
                    promoted.add(src_str)
                    logger.info(f" REPLACED (in place) | {final_path.name}")
                    continue
                moved = _promote_from_staging(it["write"], final_path)
                if not moved:
                    results[src_str] = ("error", final_str)
                    continue
                promoted.add(src_str)
            except Exception as e:
                # The promotion block above runs in THIS thread, outside
                # convert_one's own try: an unexpected failure here used to
                # kill the whole run. Settle this item as an error instead.
                results[src_str] = ("error", final_str)
                _error_details[src_str] = f"promotion failed: {e}"
                logger.error(f"  PROMOTION FAILED | {it['src'].name} | {e}")

    return results, promoted


def _read_mpg_markers(paths: list) -> dict:
    """{path str: group id | None} for the multi-page group marker
    (jxlphoto-mpg:<id>) carried in XMP-dc:Relation.

    One batched exiftool pass for the whole run — per-file spawns were
    minutes on a library. A file whose marker cannot be read comes back
    None, which the delete gate treats as "not part of any KNOWN group":
    it falls back to single-file behavior there rather than failing closed,
    because the marker read failing is not proof the file has no siblings.
    """
    mpg = {str(p): None for p in paths}
    index = {os.path.normcase(str(p)): str(p) for p in paths}
    if not paths:
        return mpg
    batch_lines = ["-j", "-s", "-s", "-XMP-dc:Relation",
                   "-charset", "FileName=UTF8", "-charset", "UTF8"]
    BATCH = 400
    for i in range(0, len(paths), BATCH):
        chunk = paths[i:i + BATCH]
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
                continue
            data = json.loads(r.stdout)
            for entry in data:
                src = entry.get("SourceFile")
                rel = entry.get("Relation")
                if src is None or rel is None:
                    continue
                values = rel if isinstance(rel, list) else [str(rel)]
                for token in values:
                    token = str(token).strip()
                    if token.startswith(MULTIPAGE_XMP_MARKER):
                        key = os.path.normcase(str(Path(src)))
                        if key in index:
                            mpg[index[key]] = token[len(MULTIPAGE_XMP_MARKER):]
                        break
        except Exception:
            continue
        finally:
            if argfile:
                try:
                    os.unlink(argfile)
                except OSError:
                    pass
    return mpg


def _delete_gate(items, results, promoted):
    """Delete sources after everything else succeeded. Fail-CLOSED on every
    check: an unverifiable output blocks the deletion, never waves it through.

    In-place items are excluded — their source was already replaced (or kept)
    inside process_group; there is nothing left to delete.

    Multi-page documents are deleted as a GROUP: every page sharing a
    jxlphoto-mpg id must pass its own gates, otherwise NO page of the group
    is deleted. Deleting page 0 while a failed page survived would spread one
    document across two folders and leave the remaining page pointing at a
    master that no longer exists.
    """
    if not DELETE_SOURCE:
        # --delete-skipped only WIDENS what --delete-source covers: without
        # it the pair is inert (main() forces DELETE_SKIPPED off and warns;
        # this belt-and-braces keeps the gate itself from ever deleting on
        # the widened flag alone).
        return

    deletables = [it for it in items if not it["in_place"]]
    mpg_of = _read_mpg_markers([it["src"] for it in deletables])

    # Pass 1: decide per file, WITHOUT deleting yet. EVERY page is recorded,
    # including the ones that will never be deleted this run (conversion
    # failed, policy skip, refused, aborted): those are exactly the siblings
    # the group veto below has to see. Leaving them out made a group whose
    # IR page FAILED look like a one-page group, and page 0 was deleted alone.
    decisions = []  # (it, status, final_path, ok, reason, settled)
    for it in deletables:
        src = it["src"]
        src_str = str(src)
        status, final_str = results.get(src_str, ("error", str(it["final"])))
        final_path = Path(final_str)
        processed_this_run = src_str in promoted
        settled = processed_this_run or (DELETE_SKIPPED and status == "skipped")
        if not settled:
            # Never deleted, never counted as a KEEP (its own failure or skip
            # is already reported) — but it still vetoes its group.
            decisions.append((it, status, final_path, False,
                              "not converted in this run", False))
            continue
        ok, reason = True, ""
        if not final_path.exists():
            ok, reason = False, "output missing"
        elif not _verify_jxl_integrity(final_path):
            ok, reason = False, "output failed integrity check"
        elif processed_this_run and it["action"] == "copy":
            # A verbatim copy is provable byte-for-byte — the strongest gate
            # there is, so it is required, not optional.
            try:
                if md5_of_file(src) != md5_of_file(final_path):
                    ok, reason = False, "copy MD5 mismatch"
            except OSError as e:
                ok, reason = False, f"copy MD5 could not be checked: {e}"
        elif VERIFY_ROUNDTRIP and status == "skipped":
            _rt_ok, detail = _verify_roundtrip_jxl(src, final_path, it["src_d"],
                                                   CJXL_DISTANCE)
            if not _rt_ok:
                ok, reason = False, f"round-trip verification failed: {detail}"
        decisions.append((it, status, final_path, ok, reason, True))

    # Pass 2: a group is only as deletable as its weakest page. The veto is
    # keyed by source path: `decisions` and the group lists hold references
    # to the same tuples, so the pass-3 loop must see the veto through a
    # separate set — reassigning the tuple inside a member list would leave
    # `decisions` untouched.
    vetoed = set()
    by_group = {}
    for dec in decisions:
        _g = mpg_of.get(str(dec[0]["src"]))
        if _g:
            # Keyed by FOLDER too, like the decoder's grouping: the id hashes
            # the source TIFF, so two copies of one split in two folders share
            # it without being the same document.
            by_group.setdefault((str(dec[0]["src"].parent), _g), []).append(dec)
    for _g, members in by_group.items():
        if all(m[3] for m in members):
            continue        # whole group passed (or a lone page): decide alone
        for m in members:
            if m[3]:
                vetoed.add(str(m[0]["src"]))

    # Pass 3: apply.
    for it, status, final_path, ok, reason, settled in decisions:
        if not settled:
            continue
        if ok and str(it["src"]) in vetoed:
            ok, reason = False, ("sibling page of the same multi-page document "
                                 "did not pass its gates — the group is kept "
                                 "together")
        src = it["src"]
        if not ok:
            _delete_stats["kept"] += 1
            logger.warning(f" KEEP ({reason}) | {src.name}")
            continue
        try:
            src.unlink()
            if status == "skipped":
                _delete_stats["deleted_archived"] += 1
            else:
                _delete_stats["deleted"] += 1
            logger.info(f" DELETED | {src.name}")
        except OSError as e:
            logger.error(f" DELETE FAILED | {src.name} | {e}")
            _delete_stats["kept"] += 1


def _preflight_space(items):
    """Advisory free-space check: the destination volume should hold the worst
    case (every output as big as its source — possible with KEEP_SMALLER's
    verbatim copies), and staging two of the largest files in flight."""
    by_volume = {}
    for it in items:
        if it["in_place"]:
            continue    # a replacement needs no net new space at the destination
        try:
            need = it["src"].stat().st_size
        except OSError:
            continue
        anchor = it["final"].parent
        while not anchor.exists() and anchor != anchor.parent:
            anchor = anchor.parent
        key = os.path.normcase(str(anchor))
        by_volume.setdefault(key, [anchor, 0])
        by_volume[key][1] += need
    for _key, (anchor, need) in by_volume.items():
        try:
            free = shutil.disk_usage(anchor).free
        except OSError:
            continue
        if free < need:
            logger.warning(f"Space preflight: {anchor} holds {_fmt_size(free)} free but the "
                           f"batch may need up to {_fmt_size(need)} there (worst case). "
                           f"The disk-full abort will stop the run if it actually fills.")
    if TEMP2_DIR is not None and items:
        largest = sorted((it["src"].stat().st_size for it in items
                          if it["src"].exists()), reverse=True)[:2]
        peak = sum(largest)
        try:
            free = shutil.disk_usage(TEMP2_DIR).free
        except OSError:
            free = None
        if free is not None and free < peak:
            logger.warning(f"Space preflight: staging {TEMP2_DIR} holds {_fmt_size(free)} "
                           f"free; up to {_fmt_size(peak)} may be in flight at once.")


def _ask_batch_resolution(asks):
    """Interactive one-shot answer for every file whose policy is "ask".

    Grouped by category so one answer covers the whole batch — prompting per
    file inside the worker pool is not an option (threads cannot prompt).
    Non-interactive stdin (wrapper, manifest, scheduled task) fails CLOSED:
    every "ask" becomes a skip.
    """
    if not asks:
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        logger.warning(f"{len(asks)} file(s) need a decision (policy 'ask') but this "
                       f"run is non-interactive — skipping them. Pass "
                       f"--on-downgrade/--on-regeneration/--on-unknown to decide "
                       f"unattended.")
        for it in asks:
            it["action"] = "skip"
        return
    by_cat = {}
    for it in asks:
        by_cat.setdefault(it["category"], []).append(it)
    for cat, group in by_cat.items():
        print()
        print(f"  {len(group)} file(s) classified as '{cat}':")
        for it in group[:5]:
            print(f"    {it['src'].name} — {it['reason']}")
        if len(group) > 5:
            print(f"    ... and {len(group) - 5} more")
        print("  [c]onvert anyway / c[o]py original / [s]kip  (default: skip)")
        try:
            answer = input("     > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        action = {"c": "convert", "o": "copy", "co": "copy", "copy": "copy",
                  "convert": "convert"}.get(answer, "skip")
        logger.info(f"Interactive decision for '{cat}' files: {action} ({len(group)})")
        for it in group:
            it["action"] = action


def main():
    global OVERWRITE, DELETE_SOURCE, DELETE_CONFIRM, VERIFY_ROUNDTRIP, DELETE_SKIPPED
    global CJXL_DISTANCE, CJXL_EFFORT, CJXL_BUFFERING, TEMP2_DIR, ENCODE_TAG_MODE
    global ON_DOWNGRADE, ON_UNKNOWN, JBRD_POLICY, KEEP_SMALLER, PROVENANCE_CHECK
    global ON_REGENERATION, EXPORT_MARKER, EXPORT_JXL_SUBFOLDER
    global _gen_divergence_logged, _error_details
    _gen_divergence_logged = False
    _error_details = {}

    parser = argparse.ArgumentParser(
        description="Batch JXL -> JXL recompressor (smaller archives, same metadata)")
    parser.add_argument("input", type=Path, nargs="?", help="Input JXL file or root folder")
    parser.add_argument("output", nargs="?", type=Path,
                        help="Output folder (modes 0 and 2 only)")
    parser.add_argument("--mode", type=int, default=0, choices=[0, 1, 2, 3, 4, 5, 6, 7, 8],
                        help="Output folder mode (0-8, same semantics as the other scripts; "
                             "8 and 0-without-output REPLACE the source JXL in place)")
    parser.add_argument("--workers", type=int, default=min(os.cpu_count() or 4, 16),
                        help="Parallel workers (default: min(CPU, 16))")
    parser.add_argument("--overwrite", action="store_true", help="Always recompress")
    parser.add_argument("--sync", action="store_true",
                        help="Recompress only when the source JXL is newer than the output")
    parser.add_argument("--distance", type=float, default=None,
                        help="Target JXL distance 0-15 (default: CJXL_DISTANCE setting, 1.0)")
    parser.add_argument("--effort", type=int, default=None, choices=range(1, 11),
                        help="cjxl effort 1-10 (default: CJXL_EFFORT setting, 7)")
    parser.add_argument("--buffering", type=int, default=None, choices=[0, 1, 2, 3],
                        help="[libjxl >= 0.12] encoder buffering level")
    parser.add_argument("--on-downgrade", dest="on_downgrade", default=None,
                        choices=["ask", "copy", "skip", "convert"],
                        help="Requested d/e cannot gain anything (same or lower distance "
                             "than an already-lossy source): ask/copy/skip/convert "
                             "(default: ON_DOWNGRADE setting, 'ask')")
    parser.add_argument("--on-regeneration", dest="on_regeneration", default=None,
                        choices=["ask", "copy", "skip", "convert"],
                        help="Source already carries a lossy generation (gen >= 1) "
                             "and this request adds another one (~1 dB each, "
                             "measured — nominal d no longer describes quality): "
                             "ask/copy/skip/convert "
                             "(default: ON_REGENERATION setting, 'ask')")
    parser.add_argument("--on-unknown", dest="on_unknown", default=None,
                        choices=["ask", "copy", "skip", "convert"],
                        help="File carries no cjxl d=/e= record: ask/copy/skip/convert "
                             "(default: ON_UNKNOWN setting, 'convert')")
    parser.add_argument("--jbrd-policy", dest="jbrd_policy", default=None,
                        choices=["copy", "skip", "convert"],
                        help="JXLs with a jbrd box (JPEG bit-exact recoverable): "
                             "copy/skip/convert (default: JBRD_POLICY setting, 'copy')")
    parser.add_argument("--no-keep-smaller", dest="no_keep_smaller", action="store_true",
                        help="Keep the re-encoded file even when it is not smaller "
                             "than the source (default: fall back to a verbatim copy)")
    parser.add_argument("--encode-tag", dest="encode_tag", default=None,
                        choices=["xmp", "software", "off"],
                        help="Where to record the new cjxl d=/e= (default: ENCODE_TAG_MODE, 'xmp')")
    parser.add_argument("--delete-source", action="store_true",
                        help="Delete each source JXL after its output is verified at "
                             "its final path (copies: MD5-matched)")
    parser.add_argument("--delete-skipped", action="store_true",
                        help="Also delete sources whose output already existed (finish "
                             "an interrupted archive)")
    parser.add_argument("--verify-roundtrip", action="store_true",
                        help="Decode both sides and compare pixels before deleting "
                             "(pixel-exact for lossless->lossless)")
    parser.add_argument("--delete-confirm-off", action="store_true",
                        help="Skip the interactive delete confirmation (the wrapper "
                             "charges its own and passes this)")
    parser.add_argument("--provenance", default=None, choices=["path", "content"],
                        help="How an existing output is matched to the source replacing "
                             "it when --delete-source runs in a collapsing mode")
    parser.add_argument("--staging", type=str, default=None,
                        help="Staging directory (fast SSD); overrides the TEMP2_DIR setting")
    parser.add_argument("--clean-staging", action="store_true",
                        help="Sweep staging leftovers older than 1h before the run")
    parser.add_argument("--no-preflight", action="store_true",
                        help="Skip the advisory free-space check")
    parser.add_argument("--export-marker", type=str, default=None,
                        help="Export marker folder name for modes 6/7 (default: _EXPORT)")
    parser.add_argument("--export-subfolder", type=str, default=None,
                        help="Mode 7: only files under EXPORT_MARKER/<subfolder>")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate: no files written, copied, replaced or deleted")
    parser.add_argument("--summary-json", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.input is None:
        parser.error("input is required")

    # Apply CLI over the script settings.
    if args.distance is not None:
        if not 0.0 <= args.distance <= 15.0:
            parser.error("--distance must be between 0 and 15")
        CJXL_DISTANCE = args.distance
    if args.effort is not None:
        CJXL_EFFORT = args.effort
    if args.buffering is not None:
        CJXL_BUFFERING = args.buffering
    if args.on_downgrade is not None:
        ON_DOWNGRADE = args.on_downgrade
    if args.on_regeneration is not None:
        ON_REGENERATION = args.on_regeneration
    if args.on_unknown is not None:
        ON_UNKNOWN = args.on_unknown
    if args.jbrd_policy is not None:
        JBRD_POLICY = args.jbrd_policy
    if args.no_keep_smaller:
        KEEP_SMALLER = False
    if args.encode_tag is not None:
        ENCODE_TAG_MODE = args.encode_tag
    if args.provenance is not None:
        PROVENANCE_CHECK = args.provenance
    if args.export_marker is not None:
        EXPORT_MARKER = args.export_marker
    if args.export_subfolder is not None:
        EXPORT_JXL_SUBFOLDER = args.export_subfolder
    if args.sync:
        OVERWRITE = "smart"
    elif args.overwrite:
        OVERWRITE = True
    if args.delete_source:
        DELETE_SOURCE = True
    if args.delete_confirm_off:
        DELETE_CONFIRM = False
    if args.verify_roundtrip:
        VERIFY_ROUNDTRIP = True
    if args.delete_skipped:
        DELETE_SKIPPED = True
    if DELETE_SKIPPED and not DELETE_SOURCE:
        # Same rule as the other three scripts: this flag only WIDENS what
        # --delete-source deletes. Armed alone it used to delete
        # already-archived sources with no confirmation and no provenance
        # check — a same-named output from a DIFFERENT photo was enough to
        # get the only copy of a source destroyed, silently and with exit 0.
        # Now it does nothing, said out loud.
        print("WARNING: --delete-skipped has no effect without --delete-source: it only "
              "widens which sources the deletion covers. Nothing will be deleted.")
        DELETE_SKIPPED = False

    # A DRY RUN validates without CREATING: a simulation that leaves two new
    # folders on disk is not a simulation. It never writes into either one, so
    # checking an existing path is enough there.
    def _check_dir(path, label):
        if path is None:
            return
        p = Path(path)
        try:
            if args.dry_run:
                if p.exists() and not p.is_dir():
                    parser.error(f"{label} is not a directory: {path}")
                return
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            parser.error(f"{label} is not usable: {path} ({e})")

    _check_dir(TEMP_DIR, "TEMP_DIR")
    _check_dir(args.staging, "staging directory")
    # The script-set staging path (the TEMP2_DIR constant at the top of this
    # file) never goes through --staging's validation: an invalid or
    # unwritable value used to crash mid-run at staging_dir.mkdir with a raw
    # traceback. Empty/None means staging is disabled — nothing to check.
    # (args.staging, validated just above, replaces TEMP2_DIR below, so this
    # only ever sees the script-set value.)
    if TEMP2_DIR:
        _check_dir(TEMP2_DIR, "TEMP2_DIR (staging)")

    log_file = setup_logger()
    _reset_abort()
    _warn_distance_clamp(CJXL_DISTANCE)

    if args.staging:
        TEMP2_DIR = Path(args.staging)

    # Tool checks. A dry run needs none of them — it only plans.
    if not args.dry_run:
        missing = [name for name in ("cjxl", "exiftool")
                   if shutil.which(name) is None and shutil.which(name + ".exe") is None]
        if missing:
            logger.error(f"Missing required tool(s): {', '.join(missing)} — see README "
                         f"(libjxl and exiftool must be on PATH)")
            sys.exit(1)
        if _get_cjxl_cmd() is None:
            logger.error("cjxl not found on PATH — see README")
            sys.exit(1)
        if VERIFY_ROUNDTRIP:
            if shutil.which("djxl") is None:
                logger.error("--verify-roundtrip needs djxl on PATH")
                sys.exit(1)
            try:
                import numpy  # noqa: F401
                import imagecodecs  # noqa: F401
            except ImportError as e:
                logger.error(f"--verify-roundtrip needs numpy and imagecodecs ({e})")
                sys.exit(1)

    logger.info(f"Input: {args.input}")
    logger.info(f"Mode: {args.mode} | distance: {CJXL_DISTANCE} | effort: {CJXL_EFFORT} | "
                f"workers: {args.workers}")
    logger.info(f"Policies: downgrade={ON_DOWNGRADE} | regeneration={ON_REGENERATION} | "
                f"unknown={ON_UNKNOWN} | "
                f"jbrd={JBRD_POLICY} | keep-smaller={KEEP_SMALLER}")
    if DELETE_SOURCE:
        logger.warning("--delete-source is ARMED: source JXLs will be deleted after "
                       "their outputs are verified")
    if args.dry_run:
        logger.info("DRY RUN — nothing will be written, copied, replaced or deleted")
        if DELETE_SOURCE:
            logger.warning("  (--delete-source is ARMED in this dry run; no deletion "
                           "will actually happen, and no confirmation is charged)")

    if args.clean_staging and not args.dry_run:
        _clean_staging(TEMP2_DIR)

    # --- Collect files -----------------------------------------------------
    single_file = args.input.is_file()
    if single_file:
        if args.input.suffix.lower() not in JXL_EXTS:
            logger.error(f"Not a JXL file: {args.input}")
            sys.exit(1)
        files = [args.input]
        input_root = args.input.parent
    elif args.mode in (0, 1):
        files = find_files_mode0(args.input)
        input_root = args.input
    elif args.mode == 6:
        files = find_jxls_mode6(args.input)
        input_root = args.input
    elif args.mode == 7:
        files = find_jxls_mode7(args.input)
        input_root = args.input
    else:
        files = find_jxls_recursive(args.input)
        input_root = args.input

    if not files:
        logger.warning("No JXL files found.")
        emit_summary_json(args.summary_json, ok=0, overwritten=0, skipped=0,
                          errors=0, log_file=log_file, dry_run=args.dry_run)
        sys.exit(0)
    _counter["total"] = len(files)
    logger.info(f"JXLs found: {len(files)}")

    # --- Plan: resolve outputs, then classify + policy per file ------------
    if single_file:
        if args.mode == 1:
            output_root = None
        else:
            output_root = args.output
    else:
        output_root = args.output if args.mode in (0, 2) else None
    if args.output is not None and args.mode not in (0, 2):
        logger.warning(f"The output positional is only honored in modes 0 and 2 "
                       f"(mode {args.mode} computes its own folders) — ignoring it")

    # Mode 2 with an output that IS the input root is not "in place" — it is
    # the worst of both layouts: mode 2 is RECURSIVE and writes every output
    # FLAT by name, so root-level files are replaced in place while files
    # from subfolders are flattened into the root, mixed with the originals
    # they came from. Refuse it.
    #
    # Mode 0 is deliberately NOT refused: it is flat (never recursive), so an
    # output equal to the input is exactly an in-place run — and it is what a
    # manifest row sends, since an empty Destination cell falls back to the
    # Source. Refusing it made every mode-0 manifest entry exit 2.
    def _same_dir(a, b) -> bool:
        return (os.path.normcase(os.path.abspath(str(a)))
                == os.path.normcase(os.path.abspath(str(b))))

    if (not single_file and args.output is not None and args.mode == 2
            and _same_dir(args.output, args.input)):
        logger.error(f"The output folder equals the input folder ({args.input}). "
                     "Mode 2 is recursive and writes flat by name: root files "
                     "would be replaced in place and files from subfolders "
                     "would land in the root, mixed with the originals. Pick a "
                     "different destination, or use mode 0 (flat) or 8 "
                     "(recursive) for a true in-place run.")
        sys.exit(2)

    items = []
    failures = []
    for f in files:
        if args.mode == 0:
            if output_root is not None:
                final_path = output_root / f.name
            else:
                final_path = f                       # in place
        elif args.mode == 1:
            final_path = f.parent / CONVERTED_JXL_FOLDER / f.name
        elif args.mode == 2:
            # Without an explicit output folder, default to a SUBFOLDER: input
            # and output share the .jxl extension here, so flattening into the
            # input root would land root-level files on top of themselves.
            final_path = (output_root or (input_root / CONVERTED_JXL_FOLDER)) / f.name
        else:
            try:
                final_path = resolve_output(f, args.mode, input_root)
            except ValueError as e:
                logger.error(str(e))
                sys.exit(2)
            if final_path is None:
                continue                             # outside the marker/subfolder
        # abspath on both sides: an absolute output next to a relative input
        # (or the reverse) names the SAME file, and missing that made the run
        # write the re-encode straight over its own input instead of taking
        # the atomic in-place path.
        in_place = _same_dir(final_path, f)
        items.append({"src": f, "final": final_path, "in_place": in_place})

    if not items:
        logger.warning("Nothing to process after folder-mode filtering.")
        emit_summary_json(args.summary_json, ok=0, overwritten=0, skipped=0,
                          errors=0, log_file=log_file, dry_run=args.dry_run)
        sys.exit(0)

    # Classification reads the encoder's d=/e= record (batched) and the jbrd box.
    param_info = _read_encode_params_batch([it["src"] for it in items])
    for it in items:
        info = param_info[str(it["src"])]
        it["desc"] = info["desc"]
        it["software"] = info["software"]
        it["src_d"] = info["params"][0] if info["params"] else None
        it["gen"] = info["gen"]
        it["category"], it["reason"] = _classify(info["params"],
                                                 CJXL_DISTANCE, CJXL_EFFORT,
                                                 gen=it["gen"])
        it["jbrd"] = has_jbrd_box(it["src"])
        if it["jbrd"] and JBRD_POLICY != "convert":
            it["reason"] = ("jbrd box present: the original JPEG is bit-exact "
                            "recoverable from this file; recompressing would "
                            "destroy that")
        it["action"] = _policy_action(it["category"], it["jbrd"])
        # Regeneration guard: the file already carries a lossy generation and
        # this request adds another. d_new > d_old compares one step at a time
        # and cannot see the accumulated loss (~1 dB per generation, measured)
        # — the gen= count can. Independent of --on-downgrade: when both fire,
        # the more conservative action wins.
        regen = _regeneration_action(it["gen"], CJXL_DISTANCE)
        if regen is not None:
            it["reason"] += (f" | already at generation {it['gen']}: another "
                             f"lossy re-encode costs ~1 dB regardless of step "
                             f"size (--on-regeneration)")
            combined = _more_conservative(it["action"], regen)
            if combined == "ask" and regen == "ask":
                it["category"] = "regeneration"   # prompt group of its own
            it["action"] = combined

    asks = [it for it in items if it["action"] == "ask"]
    if args.dry_run:
        for it in asks:
            it["action"] = "ask (dry run: would prompt)"
    else:
        _ask_batch_resolution(asks)

    # Duplicates abort: two WRITING actions onto one destination is never allowed.
    _abort_on_duplicate_outputs(
        [(it["src"], it["final"]) for it in items
         if it["action"] in ("convert", "copy")])

    # Cross-run provenance: in a collapsing mode with deletion armed, an
    # existing output must record the SAME origin as the source replacing it —
    # otherwise it is another photo's archive and the run must not overwrite it
    # and delete this source. Both sides carry the ORIGINAL source's markers
    # (the recompressor copies them verbatim), so equality is the proof.
    # Refused items leave the run, but the delete gate still has to see them:
    # a refused page vetoes the deletion of its multi-page siblings.
    provenance_refused = []
    if (DELETE_SOURCE and not args.dry_run
            and _run_collapses_structure(args.mode, args.output, args.input)):
        existing = [it for it in items
                    if it["action"] in ("convert", "copy") and not it["in_place"]
                    and it["final"].exists()]
        if existing:
            marker_info = _read_source_markers_batch(
                [it["final"] for it in existing] + [it["src"] for it in existing])
            refused = []
            for it in existing:
                out_info = marker_info[str(it["final"])]
                src_info = marker_info[str(it["src"])]
                if _markers_match(out_info, src_info):
                    continue
                refused.append(it)
                reason = ("existing output carries no matching provenance marker "
                          "— cannot prove it is this photo's archive; refusing to "
                          "overwrite it and delete the source")
                failures.append((str(it["src"]), reason))
                logger.error(f"REFUSED | {it['src'].name} | {reason}")
                _log_rejected_file(str(it["src"]), f"provenance: {reason}")
            if refused:
                refused_ids = {id(it) for it in refused}
                items = [it for it in items if id(it) not in refused_ids]
                provenance_refused = refused

    # --- Dry run: report and stop ------------------------------------------
    if args.dry_run:
        n_convert = sum(1 for it in items if it["action"] == "convert")
        n_copy = sum(1 for it in items if it["action"] == "copy")
        n_skip = len(items) - n_convert - n_copy
        for it in items:
            tag = {"convert": "RECOMPRESS", "copy": "COPY"}.get(it["action"], "SKIP")
            extra = f" ({it['reason']})" if it["action"] != "convert" else ""
            place = " (in place)" if it["in_place"] else ""
            logger.info(f" DRY | {tag} | {it['src'].name} -> {it['final']}{place}{extra}")
        if DELETE_SOURCE or DELETE_SKIPPED:
            n_del = sum(1 for it in items
                        if not it["in_place"] and it["action"] in ("convert", "copy"))
            whose = "--delete-source" if DELETE_SOURCE else "--delete-skipped"
            logger.info(f" DRY | {whose} would delete {n_del} source(s) "
                        f"after verification; in-place items replace themselves")
        emit_summary_json(args.summary_json, ok=n_convert + n_copy, overwritten=0,
                          skipped=n_skip, errors=len(failures), log_file=log_file,
                          extras={"recompressed": n_convert, "copied": n_copy},
                          failures=failures, dry_run=True)
        sys.exit(1 if failures else 0)

    # --- Confirmation (destructive runs) ------------------------------------
    any_in_place_convert = any(it["in_place"] and it["action"] == "convert"
                               for it in items)
    destructive = DELETE_SOURCE or any_in_place_convert
    if destructive and DELETE_CONFIRM:
        if any_in_place_convert and not DELETE_SOURCE:
            logger.warning("In-place recompression REPLACES the source JXLs — "
                           "the originals cannot be recovered afterwards")
        if not confirm_deletion_jxl(CJXL_DISTANCE > 0):
            logger.info("Aborted by user at the delete confirmation.")
            emit_summary_json(args.summary_json, ok=0, overwritten=0, skipped=0,
                              errors=0, log_file=log_file)
            sys.exit(3)

    # --- Preflight -----------------------------------------------------------
    if not args.no_preflight:
        _preflight_space(items)

    # --- Work ----------------------------------------------------------------
    work_items = []
    n_policy_skip = 0
    for it in items:
        if it["action"] == "skip":
            n_policy_skip += 1
            logger.info(f" SKIP (policy) | {it['src'].name} | {it['reason']}")
            continue
        if it["in_place"]:
            if TEMP2_DIR is not None:
                it["write"] = Path(TEMP2_DIR) / f"{uuid.uuid4().hex}_{it['src'].stem}.jxl"
            else:
                it["write"] = it["src"].parent / f"{uuid.uuid4().hex}_{it['src'].stem}.jxl"
        elif TEMP2_DIR is not None:
            it["write"] = Path(TEMP2_DIR) / f"{uuid.uuid4().hex}_{it['src'].stem}.jxl"
        else:
            it["write"] = it["final"]
        work_items.append(it)

    # The [n/total] progress counter must count what will actually run —
    # after folder-mode filtering and policy skips — not the raw scan count,
    # or the last file shows [38/212] on a 40-file run.
    _counter["total"] = len(work_items)

    results, promoted = {}, set()
    interrupted = False
    try:
        if work_items:
            results, promoted = process_group(work_items, args.workers)
    except KeyboardInterrupt:
        interrupted = True
        logger.error("Interrupted (Ctrl+C) — finishing the summary with what is done.")
        # Fall through to the summary with partial results; exit 130 below.

    # EVERY planned item, not just the ones that ran: policy-skipped and
    # refused pages must still veto the deletion of their multi-page siblings.
    _delete_gate(items + provenance_refused, results, promoted)

    # --- Summary -------------------------------------------------------------
    ok = sum(1 for s, _f in results.values() if s == "ok")
    copied = sum(1 for s, _f in results.values() if s == "copied")
    overwritten = sum(1 for s, _f in results.values() if s == "overwrite")
    skipped = sum(1 for s, _f in results.values() if s == "skipped") + n_policy_skip
    # "aborted" is not an error: those files were never attempted because the
    # run gave up early (disk full, interrupt). They are reported separately
    # and only real failures count towards the exit code.
    n_aborted = sum(1 for s, _f in results.values() if s == "aborted")
    errors = sum(1 for s, _f in results.values() if s == "error")
    errors += len(failures)
    for src_str, (s, _f) in results.items():
        if s in ("error", "aborted"):
            failures.append((src_str, _error_details.pop(src_str, s)))

    done_line = (f"\nDone: {ok} recompressed | {copied} copied | {overwritten} overwrites "
                 f"| {skipped} skipped | {errors} errors")
    if n_aborted:
        done_line += f" ({n_aborted} not attempted — the run had already aborted)"
    logger.info(done_line)
    if _aborted():
        logger.error(f"Run aborted early: {_aborted()}")
    if DELETE_SOURCE or DELETE_SKIPPED:
        logger.info(f"Delete stats: {_delete_stats['deleted']} deleted, "
                    f"{_delete_stats['deleted_archived']} already-archived deleted, "
                    f"{_delete_stats['kept']} kept")

    extras = {"recompressed": ok, "copied (policy/not smaller)": copied,
              "deleted sources": _delete_stats["deleted"],
              "kept sources": _delete_stats["kept"]}
    emit_summary_json(args.summary_json, ok=ok + copied, overwritten=overwritten,
                      skipped=skipped, errors=errors, log_file=log_file,
                      extras=extras, failures=failures)
    _report_staging_leftovers(TEMP2_DIR)

    if interrupted:
        sys.exit(130)
    if _aborted():
        sys.exit(2)
    sys.exit(1 if errors > 0 else 0)


if __name__ == "__main__":
    main()
