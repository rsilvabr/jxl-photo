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
import struct, base64, atexit
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


def _apply_rename(name: str, rename_from: str, rename_to: str) -> str:
    """--rename-from/--rename-to on one output FILE name — same semantics as
    the transcoder's resolve_output_convert: literal, case-sensitive, first
    occurrence, stem only (the extension never changes). A missing token
    leaves the name as it is."""
    stem, ext = os.path.splitext(name)
    if rename_from and rename_from in stem:
        stem = stem.replace(rename_from, rename_to, 1)
    return stem + ext


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
    if not marker_lower:
        # An empty marker matches nothing: without this, endswith("") is True
        # and marker_lower[0] raises IndexError. Fail closed — a run without a
        # marker must not silently treat every folder as an anchor.
        return False
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


def _validate_export_folder_name(name: str, marker: str, subfolder: str):
    """None when `name` is usable as the modes 6/7 output folder, else the reason.

    The output folder is created directly under the export marker, so it must be
    ONE plain path component; a name that itself matches the marker would be
    read as a second anchor by every later mode 6/7 scan; and a name equal to
    the requested input subfolder would write the outputs among the sources.
    """
    n = (name or "").strip()
    if not n:
        return "the folder name is empty"
    if n in (".", "..") or any(c in n for c in '/\\:*?"<>|'):
        return f"'{n}' is not a single plain folder name"
    if marker and _marker_matches(n.lower(), marker.lower()):
        return f"'{n}' matches the export marker '{marker}' — it would become a new anchor"
    if subfolder and n.lower() == subfolder.lower():
        return f"'{n}' is the input subfolder itself — outputs would land among the sources"
    return None


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
# Measured on real files: each extra lossy generation costs ~0.2-0.6 dB of
# PSNR on top of what the byte reduction alone costs (at a fixed file size,
# and growing with the number of generations), and the recorded nominal d
# stops describing the result — a 19-generation chain landed 9 dB below a
# single direct encode at the same file size. d_new > d_old cannot see this — it
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

OUTPUT_ICC = None
# Colour-converted DERIVATIVE instead of a plain recompression. None = keep the
# source colour space (the normal recompressor). Otherwise one of:
#   "sRGB"      -> built-in sRGB (Pillow/LittleCMS)
#   "AdobeRGB"  -> built-in Adobe RGB (1998)-compatible matrix/TRC profile
#   <path>      -> any RGB .icc/.icm file
# The pixels are decoded at 16 bits, converted with ImageMagick (relative
# colorimetric + black point compensation) and re-encoded at CJXL_DISTANCE,
# still 16 bits. A derivative is a separate, disposable copy: it is never
# written in place, never deletes anything, and never proves that the
# original TIFF is archived (its jxlphoto-src/srcsum markers are removed).

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

# --output-icc runtime state, reset at the top of main() (the test suite runs
# several main()s in one process).
_OUTPUT_ICC_LABEL = None     # "sRGB" / "AdobeRGB" / "icc-<md5>"
_OUTPUT_ICC_BYTES = None
_OUTPUT_ICC_PATH = None      # the profile written once to a temp file for magick
_SRGB_ICC_PATH = None        # assigned to sources that decode without any profile (A3)
_FORCE_REDERIVE = set()      # final paths whose derived marker names another target

# --resize-*/--sharpen runtime state, reset at the top of main() like the
# --output-icc trio above (the test suite runs several main()s in one process).
RESIZE_MODE = None           # "long" / "short" / "percent" / None
RESIZE_VALUE = None
ALLOW_UPSCALE = False
SHARPEN = "none"             # none / screen / print
SHARPEN_SIGMA = None         # expert overrides (deliberately OUTSIDE the label)
SHARPEN_GAIN = None
SHARPEN_THRESHOLD = None

DERIVATIVE = False           # --output-icc OR resize OR sharpen
_DERIVED_LABEL = None        # jxlphoto-derived:<recipe> for this run

# XMP dc:Relation provenance markers — the same strings the encoder writes, so
# a recompressed archive stays provable by the DECODER's delete gates.
SRC_PREFIX = "jxlphoto-src:"
SRCSUM_PREFIX = "jxlphoto-srcsum:"
# Multi-page group id, written by the encoder into every page's dc:Relation.
# Pages that share a document live or die together: the delete gate removes
# the whole group or nothing (a half-deleted group is spread across two
# folders with a dangling master page).
MULTIPAGE_XMP_MARKER = "jxlphoto-mpg:"

DERIVED_XMP_PREFIX = "jxlphoto-derived:"
# dc:Relation token on every --output-icc output: "<prefix><label>", label =
# sRGB / AdobeRGB / icc-<md5[:12]>. Marks the file as a colour-converted
# derivative (NOT an archive of the original) and records its target, so a
# run with a different target re-derives instead of trusting the old file.
ICC_INHERITED_XMP_FLAG = "jxlphoto-icc:inherited"
# Written by the encoder on pages that inherit IFD0's ICC. Meaningless on a
# derivative (it carries its own converted profile) — removed there.

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
# cjxl >= 0.12 clamps every lossy distance below this to the same value:
# --distance 0.005 ... 0.05 were measured producing BYTE-IDENTICAL output
# (VarDCT and lossy modular alike). libjxl PR #4238 set the floor so the DC
# coefficients stay inside int16 — below it the bitstream leaves Level 5 of
# the JPEG XL spec.
_MIN_EFFECTIVE_DISTANCE_PRE_012 = 0.01
# cjxl 0.11.2 (measured, 2026-09-24): only 0.005 and 0.01 were identical;
# 0.02/0.03/0.04 were real steps above 0.05 (d=0.01: +8.7 dB PSNR at 1.75x
# the size of d=0.05). Older builds share the pre-#4238 code.


def _min_effective_distance(exe: str) -> float:
    """The lossy distance floor of the cjxl that `exe` names.

    0.05 from libjxl 0.12 on, 0.01 before it. An unknown version reads as the
    current behaviour (0.05): the only consequence is a warning that may be
    one version too cautious, never a skipped encode.
    """
    v = _tool_version(exe)
    if v is not None and v[:2] < (0, 12):
        return _MIN_EFFECTIVE_DISTANCE_PRE_012
    return _MIN_EFFECTIVE_DISTANCE


def _warn_distance_clamp(distance, floor: float = _MIN_EFFECTIVE_DISTANCE) -> None:
    """Say so when a requested distance buys nothing.

    `floor` is the installed cjxl's lossy floor (_min_effective_distance):
    0.05 from libjxl 0.12 on, 0.01 before it.

    Call AFTER setup_logger(): on the module-level logger a warning falls
    through to logging.lastResort — unformatted on stderr, never in the log
    file (bug #238).
    """
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return
    if 0 < d < floor:
        logger.warning(
            f"--distance {d} behaves exactly like {floor}: this cjxl "
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
    # PID suffix: two children of the same script started in the same second
    # (two manifest entries) would otherwise open the SAME file in append;
    # the counter covers a same-process repeat inside that second.
    n = 0
    while True:
        log_file  = LOG_DIR / f"{timestamp}_{os.getpid()}{('_' + str(n)) if n else ''}.log"
        if not log_file.exists():
            break
        n += 1

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


def _read_derived_markers_batch(paths: list) -> dict:
    """{path str: label | None | False}: the jxlphoto-derived label, None when
    the file has no such token (NOT a derivative), False when it could not be
    read at all (unknown -> the caller must fail closed).

    Same batched argfile scheme as _read_source_markers_batch: one exiftool
    call per 400 files instead of one per file.
    """
    markers = {str(o): False for o in paths}
    # normcase -> the exact key the caller will look up by.
    index = {os.path.normcase(str(o)): str(o) for o in paths}
    if not paths:
        return markers
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
                logger.warning(f"Derivative check: could not read markers for a batch "
                               f"of {len(chunk)} file(s) (rc={r.returncode})")
                continue
            data = json.loads(r.stdout)
            for entry in data:
                src = entry.get("SourceFile")
                if src is None:
                    continue
                key = os.path.normcase(str(Path(src)))
                if key not in index:
                    continue
                rel = entry.get("Relation")
                label = None
                if rel is not None:
                    values = rel if isinstance(rel, list) else [str(rel)]
                    for token in values:
                        token = str(token).strip()
                        if token.startswith(DERIVED_XMP_PREFIX):
                            label = token[len(DERIVED_XMP_PREFIX):]
                            break
                markers[index[key]] = label
        except Exception as e:
            logger.warning(f"Derivative check: marker batch failed ({e}); "
                           f"{len(chunk)} file(s) cannot be verified")
        finally:
            if argfile:
                try:
                    os.unlink(argfile)
                except OSError:
                    pass
    return markers


def _markers_match(out_info: dict, src_info: dict, mode_check: str = "path") -> bool:
    """Does the existing output record the SAME origin as the source JXL in
    front of it?

    Both sides carry the encoder's jxlphoto-src/srcsum pair pointing at the
    ORIGINAL source (the TIFF), so equality means "same photo": the recompressor
    copies them verbatim, and an earlier recompress of this same source stamped
    the same ids.

    mode_check mirrors the other scripts' --provenance: "path" requires the
    recorded LOCATION (jxlphoto-src) to agree — the strict, documented default —
    while "content" accepts a matching source-BYTES id (jxlphoto-srcsum), which
    survives a moved folder. The flag used to be assigned and never read, so
    path runs quietly accepted content-only matches.
    """
    if mode_check == "content":
        return bool(out_info.get("srcsum") and src_info.get("srcsum")
                    and out_info["srcsum"] == src_info["srcsum"])
    return bool(out_info.get("src") and src_info.get("src")
                and out_info["src"] == src_info["src"])


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
    generation). stored = the LARGEST gen= token, 0 when absent or unparseable
    (a corrupt field carrying several self-corrects upward — undercounting is
    the damaging direction).
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
    stored = max((int(v) for v in _GEN_TAG_RE.findall(s)), default=0)
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
    stored gen = max of ALL gen= tokens in both fields (_GEN_TAG_RE.findall,
    like _reconcile_gen — a repeated gen= token is a real generation, never
    deduped, and reading only the first one made a restamp rewrite a gen=5
    file back down to gen=1). Entries inside a caption's running
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
        gen_tokens = _GEN_TAG_RE.findall(str(text))
        if gen_tokens:
            stored = max(stored, max(int(v) for v in gen_tokens))
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


def _classify(src_params, new_d: float, new_e: int, gen: int = 0,
              floor: float = _MIN_EFFECTIVE_DISTANCE):
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
    # Compare EFFECTIVE distances: cjxl clamps every lossy distance at or
    # below _MIN_EFFECTIVE_DISTANCE to the same output (that is what the
    # warning says), so d=0.02 and d=0.05 are the same quality on disk.
    # Comparing nominals classified a --distance 0.05 request over a d=0.02
    # source as "ok" and paid a lossy generation for byte-identical quality.
    # d=0 (lossless) is not clamped and stays 0.
    # `floor` is the INSTALLED cjxl's (0.05 from libjxl 0.12, 0.01 before).
    # The record does not say which cjxl wrote the source, so an old d=0.02
    # file is compared as if this cjxl had written it: under 0.12 that reads
    # as "same distance" and the policy copies — the conservative side.
    d_old_eff = max(d_old, floor) if d_old > 0 else 0.0
    new_d_eff = max(new_d, floor) if new_d > 0 else 0.0
    if new_d_eff > d_old_eff:
        return ("ok", f"smaller target: d={d_old} -> d={new_d} "
                      f"(one generation of lossy re-encode)")
    if new_d_eff == d_old_eff:
        clamp_note = ""
        if 0 < d_old < floor or 0 < new_d < floor:
            clamp_note = (f" (cjxl clamps lossy distances below "
                          f"{floor} to the same output)")
        if new_e < e_old:
            return ("downgrade", f"same distance d={d_old} with LOWER effort "
                                 f"{new_e} < {e_old}: bigger file, same quality, "
                                 f"plus a generation of loss{clamp_note}")
        return ("downgrade", f"same distance d={d_old}: re-encoding buys nothing "
                             f"but a generation of loss (effort {e_old}->{new_e} "
                             f"only changes compute time here){clamp_note}")
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
    onwards, where each pass adds ~0.2-0.6 dB of loss on top of what the
    byte reduction alone costs (measured at a fixed file size).

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
    by the dry-run previews: the --delete-skipped would-delete count, so a
    destructive option no longer promises deletions the run performs only for
    outputs it actually (re)writes. Skips that --delete-skipped widens are
    counted again by the caller.
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

def resolve_output(jxl_path: Path, mode: int, input_root: Path,
                   single_file: bool = False):
    # Mode 0: single file in-place — handled in main() before calling this
    # Mode 1: single file -> recompressed_jxl/ subfolder — handled in main()

    def _warn_if_outside(result: Path) -> Path:
        # Modes 4/5 can land OUTSIDE the selected input tree for files at its
        # root — surface that once per file instead of surprising the user
        # later. For a single-FILE run main() passes the file's parent as
        # input_root, and the legitimate mode-4/5 output is a SIBLING of that
        # folder: anchoring at the folder itself flagged every single-file run.
        anchor = input_root.parent if single_file else input_root
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
    """True if any directory part is one of this tool's output folder names,
    the configured EXPORT_JXL_FOLDER (modes 6/7 — a custom name would otherwise
    be re-processed as a source by the next run), or carries this tool's output
    suffix: mode 4's fallback renames a folder without the token to
    <name>_JXL_small (an exact-name set never matches those), and a recursive
    re-run used to re-encode its own output there.
    Callers pass only the parts BELOW the input root, so pointing a run AT
    such a folder to compress it again stays legitimate."""
    own = EXPORT_JXL_FOLDER.lower()
    return any(p in _RECOMPRESSOR_OUTPUT_FOLDERS
               or p == own
               or p.endswith("_" + JXL_SUFFIX_REPLACE.lower())
               for p in parts_lower)


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
        # Name the suffix too: with item 16's fix a folder like
        # "photos_JXL_small" is filtered without matching any of the exact
        # names below, and a user seeing only those names would not recognise
        # their folder in the message.
        logger.info(f"Ignored {skipped} JXL(s) inside recompressor output folders "
                    f"({', '.join(sorted(_RECOMPRESSOR_OUTPUT_FOLDERS | {EXPORT_JXL_FOLDER.lower()}))}, "
                    f"or any folder ending in _{JXL_SUFFIX_REPLACE}) — "
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


def _disk_full_need(jxl_path: Path) -> int:
    """Best-effort size estimate for _abort_if_disk_full: a source that
    vanishes between the plan and the error handler must not raise a raw
    OSError OUT of the worker's except block (that crashed before this was
    guarded) — 0 simply means nothing new to blame on a full disk."""
    try:
        return jxl_path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Output colour space (--output-icc)
#
# A colour-converted derivative is decoded at 16 bits, converted with
# ImageMagick (relative colorimetric + black point compensation) and re-encoded
# at CJXL_DISTANCE, still 16 bits. The traps these helpers exist to avoid are
# all verified behaviours of magick 7.1.2 / cjxl-djxl 0.12:
#   A1/A2: -strip or png:exclude-chunk removes the iCCP from the converted PNG,
#          and cjxl then tags the pixels as sRGB — silently wrong colours.
#   A3:    a JXL encoded as sRGB decodes to a PNG with an sRGB chunk and NO
#          iCCP; `magick in.png -profile target.icc` then ASSIGNS the target
#          instead of converting. The source profile must always be assigned
#          explicitly before the conversion.
#   A4:    the master's XMP CreatorTool carries the ORIGINAL ICC in base64; the
#          derivative must replace it with the TARGET profile, or a later decode
#          to TIFF would label the converted pixels with the wrong space.
#   A5:    cjxl 0.12 copies input PNG metadata as Brotli "brob" boxes; the
#          derivative is encoded with `-x strip=exif -x strip=xmp` and gets the
#          master's metadata copied with exiftool afterwards.
# ---------------------------------------------------------------------------

_ICC_D50 = np.array([0.9642, 1.0, 0.8249])
_BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                      [-0.7502, 1.7135, 0.0367],
                      [0.0389, -0.0685, 1.0296]])


def _icc_s15f16(v) -> bytes:
    return struct.pack(">i", int(round(float(v) * 65536.0)))


def _icc_xyz_tag(xyz) -> bytes:
    return b"XYZ " + b"\0" * 4 + b"".join(_icc_s15f16(c) for c in xyz)


def _build_matrix_trc_icc(description: str, primaries_xy, white_xy, gamma: float) -> bytes:
    """A minimal ICC v2.1 display profile: D50-adapted (Bradford) colorants,
    one gamma curve shared by R/G/B, media white = the native white (the v2
    convention Adobe's own AdobeRGB1998.icc uses — with a D50 wtpt cjxl stores
    D50-adapted 'Custom' primaries instead of the real ones)."""
    def xyz(x, y):
        return np.array([x / y, 1.0, (1.0 - x - y) / y])
    wp = xyz(*white_xy)
    prim = np.column_stack([xyz(*p) for p in primaries_xy])
    m = prim * np.linalg.solve(prim, wp)                      # RGB -> XYZ, native white
    adapt = (np.linalg.inv(_BRADFORD)
             @ np.diag((_BRADFORD @ _ICC_D50) / (_BRADFORD @ wp)) @ _BRADFORD)
    m50 = adapt @ m
    desc_ascii = description.encode("ascii") + b"\0"
    desc = (b"desc" + b"\0" * 4 + struct.pack(">I", len(desc_ascii)) + desc_ascii
            + struct.pack(">II", 0, 0) + struct.pack(">HB", 0, 0) + b"\0" * 67)
    cprt = b"text" + b"\0" * 4 + b"No copyright, use freely\0"
    trc = b"curv" + b"\0" * 4 + struct.pack(">I", 1) + struct.pack(">H", int(round(gamma * 256)))
    tags = [(b"desc", desc), (b"cprt", cprt), (b"wtpt", _icc_xyz_tag(wp)),
            (b"rXYZ", _icc_xyz_tag(m50[:, 0])), (b"gXYZ", _icc_xyz_tag(m50[:, 1])),
            (b"bXYZ", _icc_xyz_tag(m50[:, 2])),
            (b"rTRC", trc), (b"gTRC", trc), (b"bTRC", trc)]
    offset = 128 + 4 + 12 * len(tags)
    table, data, placed = b"", b"", {}
    for sig, body in tags:
        if body not in placed:                    # r/g/bTRC share one element
            while (offset + len(data)) % 4:
                data += b"\0"
            placed[body] = (offset + len(data), len(body))
            data += body
        off, ln = placed[body]
        table += sig + struct.pack(">II", off, ln)
    body = struct.pack(">I", len(tags)) + table + data
    while len(body) % 4:
        body += b"\0"
    header = (struct.pack(">I", 128 + len(body)) + b"lcms" + bytes([2, 0x10, 0, 0])
              + b"mntr" + b"RGB " + b"XYZ " + b"\0" * 12 + b"acsp" + b"MSFT"
              + b"\0" * 20 + struct.pack(">I", 0)
              + b"".join(_icc_s15f16(c) for c in _ICC_D50) + b"\0" * 48)
    if len(header) != 128:
        raise RuntimeError("internal: ICC header is not 128 bytes")
    return header + body


def _adobe_rgb_icc_bytes() -> bytes:
    return _build_matrix_trc_icc("AdobeRGB1998-compatible (jxl-photo)",
                                 [(0.6400, 0.3300), (0.2100, 0.7100), (0.1500, 0.0600)],
                                 (0.3127, 0.3290), 563 / 256)


def _resolve_output_icc(value: str):
    """(label, icc_bytes) for --output-icc, or raise ValueError with the reason.

    Aliases are case-insensitive. A file must be a real RGB profile: a CMYK or
    grey profile cannot be the target of an RGB->RGB conversion, and a random
    file must be refused BEFORE a whole batch fails on it."""
    key = value.strip().lower()
    if key == "srgb":
        try:
            from PIL import ImageCms
        except ImportError:
            raise ValueError("--output-icc sRGB needs Pillow (pip install pillow)")
        return "sRGB", ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    if key in ("adobergb", "adobe", "adobergb1998"):
        return "AdobeRGB", _adobe_rgb_icc_bytes()
    p = Path(value.strip().strip('"'))
    if not p.is_file():
        raise ValueError(f"--output-icc: not sRGB, AdobeRGB, or an existing file: {value}")
    data = p.read_bytes()
    if len(data) < 128 or data[36:40] != b"acsp":
        raise ValueError(f"--output-icc: {p} is not an ICC profile")
    if data[16:20] != b"RGB ":
        raise ValueError(f"--output-icc: {p} is a {data[16:20].decode(errors='replace').strip()} "
                         f"profile — the target must be an RGB profile")
    return "icc-" + hashlib.md5(data).hexdigest()[:12], data


def _png_chunk_types(png_path: Path) -> list:
    """Chunk types before the first IDAT (iCCP/sRGB must precede it per the PNG
    spec) — reads a few KB, not the whole 16-bit intermediate."""
    types = []
    with open(png_path, "rb") as f:
        if f.read(8) != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError(f"not a PNG: {png_path.name}")
        while True:
            head = f.read(8)
            if len(head) < 8:
                break
            n = struct.unpack(">I", head[:4])[0]
            t = head[4:8].decode("latin-1")
            if t == "IDAT":
                break
            types.append(t)
            f.seek(n + 4, 1)                         # data + CRC
    return types


def _png_is_grayscale(png_path: Path) -> bool:
    """True when the PNG djxl just wrote is single-channel (colour type 0 or 4).

    Only the 26-byte signature + IHDR is read: this runs per file, and the
    decoded intermediate of a 93 MP scan is hundreds of MB.
    """
    try:
        with open(png_path, "rb") as f:
            head = f.read(26)
    except OSError:
        return False
    if len(head) < 26 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    return head[25] in (0, 4)          # 0 = grey, 4 = grey + alpha


SHARPEN_PRESETS = {
    "screen": {"sigma": 0.5, "gain": 0.6, "threshold": 0.02},   # CALIBRATE vs C1 "screen"
    "print":  {"sigma": 1.0, "gain": 1.0, "threshold": 0.02},   # CALIBRATE vs C1 "print"
}
# The ONLY place the sharpening numbers live, calibrated against Capture One's
# own output sharpening. Units are OUTPUT pixels, so one preset works at any
# output size. sigma = gaussian sigma (px), gain = unsharp amount (1.0 = 100%),
# threshold = fraction of full scale below which a difference is left alone
# (protects sky, skin and noise).


def _png_size(png_path: Path):
    """(width, height) from the IHDR of a PNG djxl just wrote."""
    with open(png_path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise RuntimeError(f"not a PNG: {png_path}")
    return struct.unpack(">II", head[16:24])


def _resize_geometry(w: int, h: int, mode, value, allow_upscale: bool = False):
    """(new_w, new_h) for --resize-long/--resize-short/--resize-percent, or None
    when the image keeps its size (no resize requested, same size, or a
    would-be upscale without --allow-upscale). Aspect ratio always kept."""
    if not mode:
        return None
    if mode == "long":
        scale = float(value) / max(w, h)
    elif mode == "short":
        scale = float(value) / min(w, h)
    elif mode == "percent":
        scale = float(value) / 100.0
    else:
        raise ValueError(f"unknown resize mode: {mode}")
    if scale <= 0:
        raise ValueError("resize value must be positive")
    if scale > 1.0 and not allow_upscale:
        return None
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    if (nw, nh) == (w, h):
        return None
    return nw, nh


def _sharpen_args(preset, sigma=None, gain=None, threshold=None, grey=False):
    """ImageMagick args for output sharpening, or [] for none. Colour images are
    sharpened on the Lab L channel only (no colour fringes); a grey image has a
    single channel and is sharpened directly. `-colorspace Lab ... -colorspace
    sRGB` DROPS the ICC profile — the caller must re-assign the output profile
    right after these args (verified: pixels stay identical)."""
    if not preset or preset == "none":
        return []
    p = dict(SHARPEN_PRESETS[preset])
    if sigma is not None:
        p["sigma"] = float(sigma)
    if gain is not None:
        p["gain"] = float(gain)
    if threshold is not None:
        p["threshold"] = float(threshold)
    op = f"0x{p['sigma']}+{p['gain']}+{p['threshold']}"
    if grey:
        return ["-unsharp", op]
    return ["-colorspace", "Lab", "-channel", "R", "-unsharp", op, "+channel",
            "-colorspace", "sRGB"]


def _resize_label(mode, value, allow_upscale=False):
    if not mode:
        return ""
    tag = {"long": "long", "short": "short", "percent": "pct"}[mode]
    v = int(value) if float(value).is_integer() else value
    return f"@{tag}{v}" + ("+up" if allow_upscale else "")


def _derived_label(icc_label, resize_mode=None, resize_value=None,
                   allow_upscale=False, sharpen=None):
    """jxlphoto-derived:<label>. Changing any part re-derives on the next sync."""
    return ((icc_label or "keep")
            + _resize_label(resize_mode, resize_value, allow_upscale)
            + (f"+{sharpen}" if sharpen and sharpen != "none" else ""))


def _xmp_icc_from_creator_tool(text: str):
    """The encoder's ICC:<base64> segment of CreatorTool, validated — the same
    rules as jxl_tiff_decoder.extract_icc_from_xmp (split on '|', 'ICC:'
    prefix, strict base64, >= 128 bytes, 'acsp' at 36)."""
    for segment in (text or "").split("|"):
        segment = segment.strip()
        if not segment.startswith("ICC:"):
            continue
        try:
            data = base64.b64decode(segment[4:].strip(), validate=True)
        except Exception:
            continue
        if len(data) >= 128 and data[36:40] == b"acsp":
            return data
    return None


def _creator_tool_without_icc(text: str) -> str:
    """CreatorTool with every ICC:<base64> blob removed — same regexes as the
    encoder's stale-blob strip (jxl_tiff_encoder.py, 'Strip any stale
    ICC:<base64> blob'), so a trailing ' | Real App' segment is never eaten."""
    s = text or ""
    s = re.sub(r'ICC:[A-Za-z0-9+/=]+(?=\s*(\||$))', '', s, flags=re.MULTILINE).strip()
    if 'ICC:' in s and '|' not in s:
        s = re.sub(r'ICC:[A-Za-z0-9+/=]{64,}', '', s).strip()
    s = re.sub(r'\s*\|\s*$', '', s).strip()
    s = re.sub(r'^\s*\|\s*', '', s).strip()
    s = re.sub(r'\s*\|\s*\|\s*', ' | ', s)
    return s


def _read_creator_and_relation(jxl_path: Path):
    """(creator_tool str, [relation tokens]) of one file. Raises on failure:
    a derivative whose source profile cannot be read must not be produced."""
    r = _run_exiftool_argfile(["-j", "-s", "-s", "-XMP-xmp:CreatorTool",
                               "-XMP-dc:Relation", str(jxl_path)], timeout=60)
    if not r.stdout:
        raise RuntimeError(f"exiftool could not read {jxl_path.name} (rc={r.returncode})")
    entry = json.loads(r.stdout)[0]
    rel = entry.get("Relation")
    tokens = [] if rel is None else [str(t).strip() for t in (rel if isinstance(rel, list) else [rel])]
    return str(entry.get("CreatorTool") or ""), tokens


def _derive_pixels(jxl_path: Path, write_path: Path) -> tuple:
    """Decode at 16 bits, apply the derivative recipe, re-encode.

    The recipe is any of: colour conversion to --output-icc, a Lanczos resize,
    Lab-L sharpening. The SOURCE profile is always assigned explicitly (trap
    A3): the encoder's original ICC from XMP CreatorTool when present — exactly
    what jxl_tiff_decoder attaches to the same pixels — else the iCCP djxl
    wrote (extracted to a file for magick), else sRGB (djxl writes an sRGB
    chunk, no iCCP, for sRGB-encoded files). Without --output-icc there is no
    conversion: the source profile is re-assigned after the Lab sharpening pass
    (trap B2), which drops it. Grey images are never colour-converted (no gamut
    to map; an RGB profile on a single channel is invalid — same rule as the
    transcoder), but resize and sharpening still apply.

    Returns (converted, size): converted True only for a colour conversion
    (CreatorTool then carries the TARGET profile); size is the resized (w, h)
    or None. _derivative_metadata_args takes both instead of guessing again
    from the jxlphoto-grayscale marker: a grey JXL that does not carry the
    marker (not written by this toolkit) was left unconverted here but got the
    RGB target profile stamped into its CreatorTool.
    """
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
        tmp = Path(tmp)
        dec_png, conv_png = tmp / "dec.png", tmp / "conv.png"
        r = subprocess.run(["djxl", str(jxl_path), str(dec_png), "--bits_per_sample=16"],
                           capture_output=True, timeout=CJXL_TIMEOUT)
        if r.returncode != 0 or not dec_png.exists():
            raise RuntimeError(f"djxl: {(r.stderr or b'').decode(errors='replace')[:200]}")
        grey = _png_is_grayscale(dec_png)
        converted = False
        size = None
        if grey and not (RESIZE_MODE or SHARPEN != "none"):
            logger.info(f"  >Grayscale image: encoded without colour conversion | {jxl_path.name}")
            enc_in = dec_png
        else:
            args = []
            out_profile = None
            if not grey:
                creator, _tokens = _read_creator_and_relation(jxl_path)
                src_icc = _xmp_icc_from_creator_tool(creator)
                chunks = _png_chunk_types(dec_png)
                if src_icc:
                    src_path = tmp / "src.icc"
                    src_path.write_bytes(src_icc)
                    args += ["+profile", "*", "-profile", str(src_path)]
                elif "iCCP" in chunks:
                    src_path = tmp / "src_iccp.icc"
                    _r = subprocess.run(["magick", str(dec_png), str(src_path)],
                                        capture_output=True, timeout=CJXL_TIMEOUT)
                    if _r.returncode != 0 or not src_path.exists():
                        raise RuntimeError(
                            f"magick could not extract the source ICC: "
                            f"{(_r.stderr or b'').decode(errors='replace')[:200]}")
                    # convert from djxl's own iCCP, no explicit assign
                elif "sRGB" in chunks:
                    src_path = _SRGB_ICC_PATH
                    args += ["+profile", "*", "-profile", str(src_path)]
                else:
                    raise RuntimeError("cannot tell the source colour space (no XMP ICC, "
                                       "no iCCP, no sRGB chunk) — refusing to guess")
                if OUTPUT_ICC:
                    args += ["-intent", "Relative", "-black-point-compensation",
                             "-profile", str(_OUTPUT_ICC_PATH)]
                    converted = True
                    out_profile = _OUTPUT_ICC_PATH
                else:
                    out_profile = src_path
            if RESIZE_MODE:
                w, h = _png_size(dec_png)
                geom = _resize_geometry(w, h, RESIZE_MODE, RESIZE_VALUE, ALLOW_UPSCALE)
                if geom:
                    args += ["-filter", "Lanczos", "-resize", f"{geom[0]}x{geom[1]}!"]
                    size = geom
                else:
                    logger.info(f"  Already smaller than the target — kept at "
                                f"{w}×{h} | {jxl_path.name}")
            sharp = _sharpen_args(SHARPEN, SHARPEN_SIGMA, SHARPEN_GAIN,
                                  SHARPEN_THRESHOLD, grey=grey)
            args += sharp
            if sharp and not grey:
                # B2: `-colorspace Lab ... -colorspace sRGB` drops the ICC
                # profile; re-assign the one that describes the pixels now.
                args += ["-profile", str(out_profile)]
            cmd = (["magick", str(dec_png)] + args
                   + ["-depth", "16", "png:" + str(conv_png)])
            r = subprocess.run(cmd, capture_output=True, timeout=CJXL_TIMEOUT)
            if r.returncode != 0 or not conv_png.exists():
                raise RuntimeError(f"magick: {(r.stderr or b'').decode(errors='replace')[:200]}")
            if not grey and "iCCP" not in _png_chunk_types(conv_png):
                # Traps A1/A2: without the profile cjxl would silently tag the
                # converted pixels as sRGB. Never let that reach the archive.
                raise RuntimeError("ImageMagick wrote the converted image without its ICC "
                                   "profile — refusing to encode it as the wrong colour space")
            enc_in = conv_png
        cjxl_cmd = ([_get_cjxl_cmd() or "cjxl", str(enc_in), str(write_path),
                     "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT),
                     "--container=1", "-x", "strip=exif", "-x", "strip=xmp"]
                    + _cjxl_buffering_flag())
        r = subprocess.run(cjxl_cmd, capture_output=True, timeout=CJXL_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {(r.stderr or b'').decode(errors='replace')[:200]}")
    return converted, size


def _derivative_metadata_args(jxl_path: Path, converted: bool = True,
                              size=None) -> list:
    """exiftool lines that turn the copied master metadata into a derivative's:
    - dc:Relation: drop jxlphoto-src/srcsum (a derivative must never prove the
      original is archived — trap A7), drop any old jxlphoto-derived and the
      page-level icc:inherited flag, add jxlphoto-derived:<recipe>;
    - CreatorTool: replace the ICC:<b64> blob by the TARGET profile when the
      pixels were colour-converted, so the decoder labels a decoded TIFF with
      the colour space the pixels are really in (trap A4). When they were not
      (`converted` False: a grey image or a resize/sharpening-only recipe) the
      copied CreatorTool is kept — its ICC still describes the pixels;
    - pixel dimensions: corrected to the resized output's own (the XMP pair
      only when the source already carried it).
    """
    creator, tokens = _read_creator_and_relation(jxl_path)
    drop = (SRC_PREFIX, SRCSUM_PREFIX, DERIVED_XMP_PREFIX)
    keep = [t for t in tokens
            if t and not t.startswith(drop) and t != ICC_INHERITED_XMP_FLAG]
    lines = ["-XMP-dc:Relation="]
    lines += ["-XMP-dc:Relation+=" + _argfile_safe(t) for t in keep]
    lines.append("-XMP-dc:Relation+=" + DERIVED_XMP_PREFIX + _DERIVED_LABEL)
    if converted:
        base = _creator_tool_without_icc(creator)
        b64 = base64.b64encode(_OUTPUT_ICC_BYTES).decode("ascii")
        new_ct = f"{base} | ICC:{b64}" if base else f"ICC:{b64}"
        lines.append("-XMP-xmp:CreatorTool=" + _argfile_safe(new_ct))
    if size:
        nw, nh = size
        lines.append(f"-ExifImageWidth={nw}")
        lines.append(f"-ExifImageHeight={nh}")
        r = _run_exiftool_argfile(
            ["-j", "-s", "-s", "-XMP-exif:PixelXDimension",
             "-XMP-exif:PixelYDimension", str(jxl_path)], timeout=60)
        if r is not None and r.stdout:
            entry = json.loads(r.stdout)[0]
            if entry.get("PixelXDimension") is not None:
                lines.append(f"-XMP-exif:PixelXDimension={nw}")
            if entry.get("PixelYDimension") is not None:
                lines.append(f"-XMP-exif:PixelYDimension={nh}")
    return lines


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
    # next to the source as <uuid>_name.tmp, which no scan adopts as a NEW
    # input (and the REPLACE FAILED paths clean it up).
    output_dirty = False
    _pre_identity = _capture_output_identity(write_path, final_path)
    try:
        if _aborted():
            return (str(jxl_path), "aborted", str(final_path))

        if (not in_place
                and os.path.normcase(str(final_path)) not in _FORCE_REDERIVE
                and _would_skip(jxl_path, final_path)):
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
        if DERIVATIVE:
            # djxl -> magick (recipe) -> cjxl; returns (converted, size|None)
            converted, size = _derive_pixels(jxl_path, write_path)
        else:
            converted, size = True, None
            # --container=1 UNCONDITIONALLY: at d=0 the gate used to omit it, so a
            # bare-codestream source (any third-party JXL) produced a bare output
            # that the exiftool restamp below refuses to edit ("Will wrap JXL
            # codestream in ISO BMFF container for writing") — a guaranteed ERROR
            # per file. Wrapping an already-container input costs nothing.
            container_flag = ["--container=1"]
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
        _extra = (_derivative_metadata_args(jxl_path, converted, size)
                  if DERIVATIVE else [])
        r2 = _run_exiftool_argfile(
            ["-overwrite_original", "-api", "Compress=0",
             "-tagsfromfile", str(jxl_path),
             "-exif:all", "-xmp:all", "-iptc:all"]
            + _restamp_args(desc, software, label=jxl_path.name)
            + _extra
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
        if DERIVATIVE and status == "ok":
            label = f"DERIVE ({_DERIVED_LABEL})"
        else:
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
                            _disk_full_need(jxl_path))
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
                if not it["in_place"] and it["write"].parent == final_path.parent:
                    # Beside-the-final uuid temp (no staging): same volume, so
                    # os.replace is atomic and overwrites an existing output —
                    # _promote_from_staging's shutil.move would NOT (os.rename
                    # refuses an existing destination on Windows).
                    try:
                        os.replace(str(it["write"]), str(final_path))
                        moved = True
                    except OSError as e:
                        logger.error(f"  REPLACE FAILED | {final_path.name} | {e}")
                        # Remove the temp: the source is untouched (a failed
                        # replace never destroys it), so leaving the verified
                        # re-encode behind would only add a beside-final
                        # orphan no sweep ever looks at. The next run
                        # re-creates it.
                        try:
                            it["write"].unlink()
                        except OSError:
                            pass
                        moved = False
                    if not moved:
                        results[src_str] = ("error", final_str)
                        continue
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
                        # Same .tmp rule as the in-place write temps: if the
                        # promotion fails the temp stays behind (deliberately,
                        # see below) and must not be adoptable as a NEW input.
                        dest_tmp = (final_path.parent
                                    / f"{uuid.uuid4().hex}_{final_path.stem}.tmp")
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
                            # Same cleanup as above: the original is intact,
                            # the temp is worthless bytecode next to it (and
                            # .tmp-named now, but do not leave it to rot).
                            try:
                                it["write"].unlink()
                            except OSError:
                                pass
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


def _read_mpg_markers(paths: list):
    """({path str: group id | None}, complete) for the multi-page group marker
    (jxlphoto-mpg:<id>) carried in XMP-dc:Relation.

    One batched exiftool pass for the whole run — per-file spawns were
    minutes on a library. `complete` is False when any batch could not be
    read: the delete gate then treats EVERY source as unverifiable (nothing
    is deleted), because an unreadable page cannot be linked to its group —
    and a page wrongly treated as a lone standalone could be deleted while
    the siblings that complete its document are kept.
    """
    mpg = {str(p): None for p in paths}
    index = {os.path.normcase(str(p)): str(p) for p in paths}
    if not paths:
        return mpg, True
    batch_lines = ["-j", "-s", "-s", "-XMP-dc:Relation",
                   "-charset", "FileName=UTF8", "-charset", "UTF8"]
    BATCH = 400
    complete = True
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
            if r.returncode != 0 or not r.stdout:
                complete = False
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
            complete = False
            continue
        finally:
            if argfile:
                try:
                    os.unlink(argfile)
                except OSError:
                    pass
    return mpg, complete


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
    mpg_of, mpg_complete = _read_mpg_markers([it["src"] for it in deletables])
    if not mpg_complete:
        # Fail closed: an unreadable marker cannot prove a page is standalone,
        # so no source in this run may be deleted. Without this, a page whose
        # marker read failed was treated as a lone group and deleted while the
        # siblings that complete its document could be kept (or vice versa).
        _delete_stats["kept"] += len(deletables)
        logger.error("Group markers could not be read for the whole run — "
                     "NOTHING will be deleted (fail closed). Fix the exiftool "
                     "failure and re-run; the outputs are already written.")
        return

    # Cross-run provenance for the --delete-skipped path: a SKIPPED source is
    # deleted on the strength of a PRE-EXISTING output this run never wrote.
    # Verifying that output's structure alone would delete a source whose
    # same-named "archive" is an unrelated photo's output (real data loss — the
    # modes that collapse folder structure already refuse this at the plan
    # stage, but modes 1/3 write beside the source and had no proof at all).
    # The recompressor copies the source's jxlphoto-src/srcsum markers verbatim
    # into its output, so marker equality is the proof. Only action "convert"
    # needs it: a copy is proven byte-for-byte by the MD5 gate below. One
    # batched exiftool pass for the whole run; an unreadable marker fails
    # CLOSED (the source is kept).
    _prov_needed = [it for it in deletables
                    if DELETE_SKIPPED
                    and results.get(str(it["src"]), ("error",))[0] == "skipped"
                    and it["action"] == "convert"
                    and Path(results.get(str(it["src"]),
                                          ("error", str(it["final"])))[1]).exists()]
    _prov_info = {}
    if _prov_needed:
        _prov_paths = []
        for it in _prov_needed:
            _prov_paths.append(it["final"])
            _prov_paths.append(it["src"])
        _prov_marks = _read_source_markers_batch(_prov_paths)
        for it in _prov_needed:
            _prov_info[id(it)] = (
                _prov_marks.get(str(it["final"])) or {"src": None, "srcsum": None},
                _prov_marks.get(str(it["src"])) or {"src": None, "srcsum": None},
            )

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
        elif it["action"] == "copy" or status == "copied":
            # A verbatim copy is provable byte-for-byte — the strongest gate
            # there is, so it is required, not optional. Status "copied" covers
            # the KEEP_SMALLER fallback too: action stays "convert", but the
            # output is a byte-copy of the source and must pass the same check.
            # The MD5 is also required when the output PREDATES this run
            # (status "skipped", admitted by --delete-skipped with an action
            # of "copy"): without it that source was deleted on the strength
            # of an integrity check alone, with nothing proving the
            # pre-existing output is a verbatim copy of it. Fail closed: no
            # proof, no delete.
            try:
                if md5_of_file(src) != md5_of_file(final_path):
                    ok, reason = False, "copy MD5 mismatch"
            except OSError as e:
                ok, reason = False, f"copy MD5 could not be checked: {e}"
        elif status == "skipped":
            # The source is deleted on the strength of a PRE-EXISTING output
            # this run never wrote. For a skipped CONVERT that proof is the
            # provenance marker pair the recompressor copies verbatim from the
            # source: without it a valid, newer same-named output from an
            # unrelated photo would certify the deletion. The modes that
            # collapse folder structure already refuse this at the plan stage,
            # but modes 1/3 write beside the source and had no check at all.
            # A skipped COPY never reaches here (the MD5 branch above owns it).
            out_info, src_info = _prov_info.get(id(it), (None, None))
            if out_info is None:
                ok, reason = False, ("existing output's provenance marker could "
                                     "not be read")
            elif not _markers_match(out_info, src_info, PROVENANCE_CHECK):
                ok, reason = False, ("existing output carries no matching "
                                     "provenance marker")
            elif VERIFY_ROUNDTRIP:
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
        # Same guard the first loop above has: a source can vanish between the
        # plan and this estimate (another process, a concurrent --delete-source
        # run). A raw OSError here escaped main() with a traceback, no summary
        # and no documented exit code.
        sizes = []
        for it in items:
            try:
                sizes.append(it["src"].stat().st_size)
            except OSError:
                continue  # vanished between the plan and the preflight
        largest = sorted(sizes, reverse=True)[:2]
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
    global ON_REGENERATION, EXPORT_MARKER, EXPORT_JXL_SUBFOLDER, EXPORT_JXL_FOLDER
    global OUTPUT_ICC
    global _gen_divergence_logged, _error_details
    global _OUTPUT_ICC_LABEL, _OUTPUT_ICC_BYTES, _OUTPUT_ICC_PATH, _SRGB_ICC_PATH
    global _FORCE_REDERIVE
    global RESIZE_MODE, RESIZE_VALUE, ALLOW_UPSCALE
    global SHARPEN, SHARPEN_SIGMA, SHARPEN_GAIN, SHARPEN_THRESHOLD
    global DERIVATIVE, _DERIVED_LABEL
    _gen_divergence_logged = False
    _error_details = {}
    _OUTPUT_ICC_LABEL = None
    _OUTPUT_ICC_BYTES = None
    _OUTPUT_ICC_PATH = None
    _SRGB_ICC_PATH = None
    _FORCE_REDERIVE = set()
    RESIZE_MODE = None
    RESIZE_VALUE = None
    ALLOW_UPSCALE = False
    SHARPEN = "none"
    SHARPEN_SIGMA = None
    SHARPEN_GAIN = None
    SHARPEN_THRESHOLD = None
    DERIVATIVE = False
    _DERIVED_LABEL = None

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
                        help="Source already carries a lossy generation (gen >= 2 — "
                             "encoder outputs are born at gen=1, so the first "
                             "recompression is expected) and this request adds "
                             "another one (each adds ~0.2-0.6 dB of loss on top of "
                             "the byte savings, measured — and the nominal d no "
                             "longer describes quality): ask/copy/skip/convert "
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
    parser.add_argument("--output-icc", type=str, default=None,
                        help="Write a colour-converted DERIVATIVE: sRGB, AdobeRGB, or a "
                             "path to an RGB .icc file. 16-bit, converted from the "
                             "source's own profile (relative colorimetric + BPC). "
                             "Never in place, never with --delete-source.")
    _resize_group = parser.add_mutually_exclusive_group()
    _resize_group.add_argument("--resize-long", type=int, default=None, metavar="PX",
                               help="Output long edge in pixels (aspect kept; never "
                                    "upscales without --allow-upscale)")
    _resize_group.add_argument("--resize-short", type=int, default=None, metavar="PX",
                               help="Output short edge in pixels")
    _resize_group.add_argument("--resize-percent", type=float, default=None, metavar="P",
                               help="Output size as a percentage of the source")
    parser.add_argument("--allow-upscale", action="store_true",
                        help="Let --resize-* enlarge an image smaller than the target")
    parser.add_argument("--sharpen", choices=["none", "screen", "print"], default="none",
                        help="Output sharpening after the resize (Lab L channel only)")
    parser.add_argument("--sharpen-sigma", type=float, default=None,
                        help="Override the preset sigma (px)")
    parser.add_argument("--sharpen-gain", type=float, default=None,
                        help="Override the preset gain (1.0 = 100%%)")
    parser.add_argument("--sharpen-threshold", type=float, default=None,
                        help="Override the preset threshold (0-1)")
    parser.add_argument("--rename-from", type=str, default="",
                        help="Replace this text in each output file name (literal, "
                             "case-sensitive, first occurrence, extension untouched)")
    parser.add_argument("--rename-to", type=str, default="",
                        help="Replacement for --rename-from (may be empty)")
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
    parser.add_argument("--export-jxl-folder", type=str, default=None,
                        help="[Modes 6/7] Output folder created under the export marker "
                             "(default: script setting EXPORT_JXL_FOLDER, '16B_JXL_small'). "
                             "Overrides EXPORT_JXL_FOLDER.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate: no files written, copied, replaced or deleted")
    parser.add_argument("--summary-json", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.input is None:
        parser.error("input is required")

    if not args.input.exists():
        # Same treatment as the other three scripts: without it a typo'd preset
        # path rglobbed into 0 files, printed "No JXL files found." and exited 0
        # — an unattended run reported success for nothing.
        parser.error(f"input path does not exist: {args.input}")

    if args.workers < 1:
        parser.error("--workers must be >= 1")

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
    if args.output_icc is not None:
        OUTPUT_ICC = args.output_icc.strip() or None
    if args.encode_tag is not None:
        ENCODE_TAG_MODE = args.encode_tag
    if args.provenance is not None:
        PROVENANCE_CHECK = args.provenance
    if args.export_marker is not None:
        # `is not None`, mirroring --export-subfolder: `if args.export_marker:`
        # treated --export-marker "" (an explicit "export NOTHING here") as
        # absent and silently kept the default marker instead.
        EXPORT_MARKER = args.export_marker
    if args.export_subfolder is not None:
        EXPORT_JXL_SUBFOLDER = args.export_subfolder
    if args.export_jxl_folder is not None:
        EXPORT_JXL_FOLDER = args.export_jxl_folder.strip()

    if args.mode in (6, 7):
        _why = _validate_export_folder_name(EXPORT_JXL_FOLDER, EXPORT_MARKER,
                                            EXPORT_JXL_SUBFOLDER if args.mode == 7 else "")
        if _why:
            parser.error(f"--export-jxl-folder: {_why}")
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
    if args.provenance is not None and not DELETE_SOURCE:
        # Same spirit as the --delete-skipped warning above: --provenance is
        # only READ by the cross-run provenance gate, which runs solely under
        # --delete-source in a folder-collapsing mode. Armed alone it used to
        # sit silently inert while the user believed their archive was guarded.
        print("WARNING: --provenance has no effect without --delete-source: it only "
              "checks an existing output's provenance before that source is "
              "deleted. Nothing will be checked.")

    if OUTPUT_ICC:
        try:
            _OUTPUT_ICC_LABEL, _OUTPUT_ICC_BYTES = _resolve_output_icc(OUTPUT_ICC)
        except ValueError as e:
            parser.error(str(e))

    # --resize-*/--sharpen: resolve to module globals (mutual exclusion is
    # charged by argparse itself; the positivity checks exit 2 like the other
    # argument errors).
    if args.resize_long is not None:
        if args.resize_long <= 0:
            parser.error("--resize-long must be a positive number of pixels")
        RESIZE_MODE, RESIZE_VALUE = "long", args.resize_long
    elif args.resize_short is not None:
        if args.resize_short <= 0:
            parser.error("--resize-short must be a positive number of pixels")
        RESIZE_MODE, RESIZE_VALUE = "short", args.resize_short
    elif args.resize_percent is not None:
        if args.resize_percent <= 0:
            parser.error("--resize-percent must be positive")
        if args.resize_percent > 100 and not args.allow_upscale:
            parser.error("--resize-percent > 100 would upscale; add --allow-upscale "
                         "to allow it")
        RESIZE_MODE, RESIZE_VALUE = "percent", args.resize_percent
    ALLOW_UPSCALE = bool(args.allow_upscale)
    if args.allow_upscale and RESIZE_MODE is None:
        print("WARNING: --allow-upscale has no effect without --resize-long/"
              "--resize-short/--resize-percent: nothing to enlarge.")
    SHARPEN = args.sharpen
    SHARPEN_SIGMA = args.sharpen_sigma
    SHARPEN_GAIN = args.sharpen_gain
    SHARPEN_THRESHOLD = args.sharpen_threshold
    if SHARPEN == "none" and any(v is not None for v in
                                 (args.sharpen_sigma, args.sharpen_gain,
                                  args.sharpen_threshold)):
        print("WARNING: --sharpen-sigma/--sharpen-gain/--sharpen-threshold have no "
              "effect without --sharpen screen|print.")

    # A derivative is any pixel-shaping output: colour conversion, resize or
    # sharpening. Every invariant below keys off this, not off --output-icc.
    DERIVATIVE = bool(OUTPUT_ICC or RESIZE_MODE or SHARPEN != "none")
    _DERIVED_LABEL = _derived_label(_OUTPUT_ICC_LABEL, RESIZE_MODE, RESIZE_VALUE,
                                    ALLOW_UPSCALE, SHARPEN)
    _der_what = "--output-icc" if OUTPUT_ICC else "--resize-*/--sharpen"

    if DERIVATIVE and (DELETE_SOURCE or args.delete_skipped):
        parser.error(f"{_der_what} writes a derivative, it never replaces or deletes "
                     f"the source: drop --delete-source")
    if DERIVATIVE and VERIFY_ROUNDTRIP:
        parser.error("--verify-roundtrip compares pixels with the source and cannot "
                     "apply to a derivative")
    if DERIVATIVE and args.mode == 8:
        parser.error(f"{_der_what} cannot run in mode 8 (in place): pick a mode that "
                     f"writes to another folder (1-7)")

    if args.rename_to and not args.rename_from:
        parser.error("--rename-to needs --rename-from")
    if any(c in (args.rename_from + args.rename_to) for c in '/\\:*?"<>|'):
        parser.error("--rename-from/--rename-to must not contain path characters")

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
    _floor = _min_effective_distance(_get_cjxl_cmd() or "cjxl")
    _warn_distance_clamp(CJXL_DISTANCE, _floor)

    if args.export_jxl_folder is not None and args.mode not in (6, 7):
        logger.warning(f"--export-jxl-folder only applies to modes 6/7 — ignored in "
                       f"mode {args.mode}.")

    if args.staging:
        TEMP2_DIR = Path(args.staging)

    # Tool checks. A dry run needs none of them — it only plans.
    if not args.dry_run:
        # exiftool resolves through _get_exiftool_cmd(), like the other three
        # scripts: the stock Windows download ships as exiftool(-k).exe /
        # exiftool-k, which a bare which("exiftool") misses and made the
        # script refuse to run with the tool very much present.
        missing = [name for name in ("cjxl", "exiftool")
                   if shutil.which(_get_exiftool_cmd() if name == "exiftool"
                                   else name) is None]
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
        if DERIVATIVE:
            missing_icc = [t for t in ("djxl", "magick") if shutil.which(t) is None]
            if missing_icc:
                logger.error(f"{_der_what} needs {', '.join(missing_icc)} on PATH "
                             f"(djxl decodes the master, ImageMagick shapes it)")
                sys.exit(1)
            # The target profile and the sRGB fallback of A3 live in files for
            # magick; written once per run, removed at exit.
            _icc_dir = Path(tempfile.mkdtemp(prefix="jxlrec_icc_", dir=TEMP_DIR))
            atexit.register(shutil.rmtree, str(_icc_dir), True)
            if OUTPUT_ICC:
                _OUTPUT_ICC_PATH = _icc_dir / f"target_{_OUTPUT_ICC_LABEL}.icc"
                _OUTPUT_ICC_PATH.write_bytes(_OUTPUT_ICC_BYTES)
            try:
                from PIL import ImageCms
            except ImportError:
                logger.error("derivatives need Pillow: the sRGB profile assigned to "
                             "sources that decode without one comes from Pillow "
                             "(pip install pillow)")
                sys.exit(1)
            _SRGB_ICC_PATH = _icc_dir / "srgb.icc"
            _SRGB_ICC_PATH.write_bytes(
                ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())

    if DERIVATIVE:
        KEEP_SMALLER = False
        logger.info("keep-smaller disabled: a verbatim copy would not be the "
                    "requested derivative")
        if args.workers > 4:
            logger.warning(f"Derivatives with --workers {args.workers}: each worker "
                           f"holds two 16-bit PNGs plus an ImageMagick process in "
                           f"memory (~1 GB per worker on a 45 MP photo). Consider "
                           f"--workers 4.")

    logger.info(f"Input: {args.input}")
    logger.info(f"Mode: {args.mode} | distance: {CJXL_DISTANCE} | effort: {CJXL_EFFORT} | "
                f"workers: {args.workers} | output ICC: {_OUTPUT_ICC_LABEL or 'keep source'}")
    if DERIVATIVE:
        logger.info(f"Derivative: {_DERIVED_LABEL} | resize: "
                    f"{_resize_label(RESIZE_MODE, RESIZE_VALUE, ALLOW_UPSCALE) or 'none'}"
                    f" | sharpen: {SHARPEN}")
    logger.info(f"Policies: downgrade={ON_DOWNGRADE} | regeneration={ON_REGENERATION} | "
                f"unknown={ON_UNKNOWN} | "
                f"jbrd={JBRD_POLICY} | keep-smaller={KEEP_SMALLER}")
    if (OUTPUT_ICC and args.mode == 7 and EXPORT_JXL_FOLDER == "16B_JXL_small"
            and _OUTPUT_ICC_LABEL.lower() not in EXPORT_JXL_FOLDER.lower()):
        logger.info(f"--output-icc target '{_OUTPUT_ICC_LABEL}' is not part of the "
                    f"output folder name '{EXPORT_JXL_FOLDER}': consider "
                    f"--export-jxl-folder 16B_JXL_{_OUTPUT_ICC_LABEL} so the "
                    f"derivative folder says what colour space it holds")
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
    n_rename_missing = 0
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
                final_path = resolve_output(f, args.mode, input_root, single_file)
            except ValueError as e:
                logger.error(str(e))
                sys.exit(2)
            if final_path is None:
                continue                             # outside the marker/subfolder
        if args.rename_from:
            if _same_dir(final_path, f):
                # In place (mode 8, mode 0 without an output folder): a renamed
                # "replacement" is a NEW file beside the source — the recursive
                # scan would pick it up as a fresh input next run, and the
                # source would never be replaced. Refuse the whole run.
                logger.error("--rename-from cannot be used in place (mode 8, or mode 0 "
                             "without an output folder): the renamed file would sit "
                             "beside its source and be re-processed as a new input.")
                sys.exit(2)
            new_name = _apply_rename(final_path.name, args.rename_from, args.rename_to)
            if not os.path.splitext(new_name)[0].strip():
                logger.error(f"--rename-from/--rename-to would leave an empty file name "
                             f"for {f.name}")
                sys.exit(2)
            if new_name == final_path.name:
                n_rename_missing += 1
            final_path = final_path.with_name(new_name)
        # abspath on both sides: an absolute output next to a relative input
        # (or the reverse) names the SAME file, and missing that made the run
        # write the re-encode straight over its own input instead of taking
        # the atomic in-place path.
        in_place = _same_dir(final_path, f)
        items.append({"src": f, "final": final_path, "in_place": in_place})

    if args.rename_from:
        logger.info(f"Filename rename: '{args.rename_from}' -> '{args.rename_to}'")
        if n_rename_missing:
            logger.warning(f"'{args.rename_from}' not found in {n_rename_missing} file "
                           f"name(s) — those keep their names")

    # A derivative is written NEXT TO the master, never over it: an in-place
    # item (mode 8, or mode 0 without an output folder) would replace the
    # source with the shaped file and destroy the master.
    if DERIVATIVE and any(it["in_place"] for it in items):
        logger.error(f"{_der_what} cannot run in place (mode 8, or mode 0 without an "
                     f"output folder): it writes a derivative, and replacing the "
                     f"source would destroy the master. Pick a mode that writes to "
                     f"another folder (1-7).")
        sys.exit(2)

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
                                                 gen=it["gen"], floor=_floor)
        it["jbrd"] = has_jbrd_box(it["src"])
        if it["jbrd"] and JBRD_POLICY != "convert":
            it["reason"] = ("jbrd box present: the original JPEG is bit-exact "
                            "recoverable from this file; recompressing would "
                            "destroy that")
        elif it["jbrd"]:
            # --jbrd-policy convert: the user overrode the preservation, but the
            # README promises this is logged PER FILE — once re-encoded, the
            # bit-exact JPEG recovery is gone for good.
            it["reason"] += (" | jbrd box present: recompressing DESTROYS the "
                             "bit-exact JPEG recovery (--jbrd-policy convert)")
            logger.warning(f"jbrd JPEG recovery will be DESTROYED | "
                           f"{it['src'].name} | --jbrd-policy convert")
        it["action"] = _policy_action(it["category"], it["jbrd"])
        # Regeneration guard: the file already carries a lossy generation and
        # this request adds another. d_new > d_old compares one step at a time
        # and cannot see the accumulated loss (~0.2-0.6 dB per generation on
        # top of the byte savings, measured at a fixed file size)
        # — the gen= count can. Independent of --on-downgrade: when both fire,
        # the more conservative action wins.
        regen = _regeneration_action(it["gen"], CJXL_DISTANCE)
        if regen is not None:
            it["reason"] += (f" | already at generation {it['gen']}: another "
                             f"lossy re-encode adds ~0.2-0.6 dB of loss on top "
                             f"of the byte savings (--on-regeneration)")
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

    # In a derivative run a verbatim copy would NOT be the requested recipe:
    # the policy answer "copy" (from --on-downgrade/--on-regeneration/
    # --jbrd-policy, or an interactive answer) becomes a skip. Keep-smaller is
    # off too (main() above), same reason.
    if DERIVATIVE:
        for it in items:
            if it["action"] == "copy":
                it["action"] = "skip"
                it["reason"] += (f" | {_der_what}: a verbatim copy would not be "
                                 f"the requested derivative")

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
    refused_ids = set()

    # A derivative run never overwrites a file that is not one of its own
    # derivatives — even with --overwrite. Pointing --export-jxl-folder at the
    # master folder would otherwise destroy the masters in place.
    derived_refused = []
    if DERIVATIVE:
        existing = [it for it in items
                    if it["action"] in ("convert",) and it["final"].exists()]
        if existing:
            labels = _read_derived_markers_batch([it["final"] for it in existing])
            for it in existing:
                lab = labels.get(str(it["final"]), False)
                if lab is False or lab is None:
                    reason = (f"an existing file at the destination is not a derivative "
                              f"of this run (wanted jxlphoto-derived:{_DERIVED_LABEL}, or "
                              f"its markers cannot be read) — refusing to overwrite what "
                              f"may be an archive")
                    failures.append((str(it["src"]), reason))
                    derived_refused.append(it)
                    if args.dry_run:
                        logger.info(f" DRY | would REFUSE | {it['src'].name} | {reason}")
                    else:
                        logger.error(f"REFUSED | {it['src'].name} | {reason}")
                        _log_rejected_file(str(it["src"]), f"derivative: {reason}")
                elif lab != _DERIVED_LABEL:
                    _FORCE_REDERIVE.add(os.path.normcase(str(it["final"])))
                    logger.info(f" derivative recipe changed ({lab} -> {_DERIVED_LABEL}): "
                                f"re-deriving | {it['src'].name}")
            if derived_refused:
                _ids = {id(it) for it in derived_refused}
                items = [it for it in items if id(it) not in _ids]
    # Also runs in a dry run — as a PREVIEW: the refusals are reported (and
    # counted in the summary's errors) but no item leaves the plan. Gating this
    # on `not args.dry_run` made the simulation promise outputs the real run
    # would refuse (the decoder and the encoder already preview theirs).
    if (DELETE_SOURCE
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
                if _markers_match(out_info, src_info, PROVENANCE_CHECK):
                    continue
                refused.append(it)
                reason = ("existing output carries no matching provenance marker "
                          "— cannot prove it is this photo's archive; refusing to "
                          "overwrite it and delete the source")
                failures.append((str(it["src"]), reason))
                if args.dry_run:
                    logger.info(f" DRY | would REFUSE | {it['src'].name} | {reason}")
                else:
                    logger.error(f"REFUSED | {it['src'].name} | {reason}")
                    _log_rejected_file(str(it["src"]), f"provenance: {reason}")
            if refused:
                refused_ids = {id(it) for it in refused}
                if not args.dry_run:
                    items = [it for it in items if id(it) not in refused_ids]
                    provenance_refused = refused

    # --- Dry run: report and stop ------------------------------------------
    if args.dry_run:
        # A refused item stays in `items` (its DRY | would REFUSE line was
        # already printed), but it must not inflate the plan or the would-delete
        # count: the real run removes it and deletes nothing for it.
        _plan = [it for it in items if id(it) not in refused_ids]

        def _dry_would_skip(it):
            # Mirrors convert_one's SKIP decision (which never applies to an
            # in-place item): an existing, up-to-date output means the real run
            # reports SKIP, not a conversion. A derivative item whose existing
            # output names ANOTHER recipe is re-derived, not skipped.
            return (not it["in_place"] and it["action"] in ("convert", "copy")
                    and os.path.normcase(str(it["final"])) not in _FORCE_REDERIVE
                    and _would_skip(it["src"], it["final"]))

        # A skipped CONVERT admitted by --delete-skipped is deleted only when
        # its pre-existing output carries this source's markers (the real gate
        # now requires it in every mode); the preview must not promise a
        # deletion the run will KEEP. A skipped COPY is proven by the MD5.
        _dry_prov_ok = {}
        if DELETE_SOURCE and DELETE_SKIPPED:
            _sk = [it for it in _plan if _dry_would_skip(it)
                   and it["action"] == "convert" and it["final"].exists()]
            if _sk:
                _pm = _read_source_markers_batch(
                    [it["final"] for it in _sk] + [it["src"] for it in _sk])
                for it in _sk:
                    _dry_prov_ok[id(it)] = _markers_match(
                        _pm.get(str(it["final"])) or {"src": None, "srcsum": None},
                        _pm.get(str(it["src"])) or {"src": None, "srcsum": None},
                        PROVENANCE_CHECK)

        def _dry_deletable(it):
            # Mirrors the real _delete_gate for one item (multi-page veto is
            # not previewed here, as before).
            if it["in_place"] or it["action"] not in ("convert", "copy"):
                return False
            ws = _would_skip(it["src"], it["final"])
            if not DELETE_SKIPPED and ws:
                return False
            if DELETE_SKIPPED and ws and it["action"] == "convert":
                return _dry_prov_ok.get(id(it), False)
            return True

        n_convert = sum(1 for it in _plan if it["action"] == "convert"
                        and not _dry_would_skip(it))
        n_copy = sum(1 for it in _plan if it["action"] == "copy"
                     and not _dry_would_skip(it))
        n_skip = len(_plan) - n_convert - n_copy
        for it in _plan:
            if _dry_would_skip(it):
                logger.info(f" DRY | SKIP (exists) | {it['src'].name}")
                continue
            tag = {"convert": "RECOMPRESS", "copy": "COPY"}.get(it["action"], "SKIP")
            if DERIVATIVE and it["action"] == "convert":
                tag = f"DERIVE ({_DERIVED_LABEL})"
            extra = f" ({it['reason']})" if it["action"] != "convert" else ""
            place = " (in place)" if it["in_place"] else ""
            logger.info(f" DRY | {tag} | {it['src'].name} -> {it['final']}{place}{extra}")
        if DELETE_SOURCE or DELETE_SKIPPED:
            # Mirror the real delete gate: an item whose output already exists
            # and would SKIP inside convert_one is deleted only when
            # --delete-skipped widens the gate, and a skipped CONVERT needs the
            # provenance proof. Counting those made the preview promise
            # deletions the run would not perform.
            n_del = sum(1 for it in _plan if _dry_deletable(it))
            whose = "--delete-source" if DELETE_SOURCE else "--delete-skipped"
            logger.info(f" DRY | {whose} would delete {n_del} source(s) "
                        f"after verification; in-place items replace themselves")
        emit_summary_json(args.summary_json, ok=n_convert + n_copy, overwritten=0,
                          skipped=n_skip, errors=len(failures), log_file=log_file,
                          extras={"recompressed": n_convert, "copied": n_copy},
                          failures=failures, dry_run=True)
        # A simulation exits 0 even with predicted failures (the refusals above):
        # nothing happened, and errors>0 is already in the JSON summary — same
        # contract as the encoder's dry run.
        sys.exit(0)

    # --- Confirmation (destructive runs) ------------------------------------
    any_in_place_convert = any(it["in_place"] and it["action"] == "convert"
                               for it in items)
    destructive = DELETE_SOURCE or any_in_place_convert

    def _plan_touches_sources():
        """Would this plan actually WRITE into a source, or HAND a source to
        the delete gate?

        The old prompt ran BEFORE the plan was known: a no-TTY re-run of an
        already-archived folder (--delete-source, no --delete-skipped) was
        asked for a token it could not answer, exited 3, and would exit 3
        FOREVER — all without a single deletion pending. Now the confirmation
        runs only when the plan contains at least one item that converts in
        place (replacing its source) or converts/copies for real, or one that
        delete-skipped would try to delete. Fail closed: anything uncertain
        counts as a reason to ask."""
        for it in items:
            if it["action"] not in ("convert", "copy"):
                continue
            if it["in_place"]:
                return True
            if not _would_skip(it["src"], it["final"]):
                return True
            if DELETE_SKIPPED and it["final"].exists():
                return True
        return False

    if destructive and DELETE_CONFIRM and _plan_touches_sources():
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
            # In-place temps wear a .tmp suffix, never a real .jxl name (the
            # v2.1.1 --repair-jbrd pattern): a run killed externally used to
            # leave a complete-looking JXL beside the source (or in staging,
            # when TEMP2_DIR points inside the archive), which the next run
            # picked up as a NEW input and paid another generation for.
            # os.replace does not care about the extension.
            if TEMP2_DIR is not None:
                it["write"] = Path(TEMP2_DIR) / f"{uuid.uuid4().hex}_{it['src'].stem}.tmp"
            else:
                it["write"] = it["src"].parent / f"{uuid.uuid4().hex}_{it['src'].stem}.tmp"
        elif TEMP2_DIR is not None:
            # Same .tmp rule as the in-place temps above: a run killed
            # externally must not leave a complete-looking .jxl lying in the
            # temp dir for the next run to adopt as a NEW input. os.replace
            # does not care about the extension.
            it["write"] = Path(TEMP2_DIR) / f"{uuid.uuid4().hex}_{it['src'].stem}.tmp"
        else:
            # Never write under the final name: a run killed externally
            # (idle-timeout kill, Ctrl+C, power loss) would leave a TRUNCATED
            # file at the final path with a NEW mtime, which the next smart
            # sync then treats as up to date forever. A uuid temp BESIDE the
            # final, promoted with an atomic same-folder os.replace (handled
            # in process_group), means the final name only ever names a
            # verified, complete file. The temp wears a .tmp suffix, never the
            # real .jxl name (the in-place temps above, same rule): an orphan
            # beside the final would otherwise be adoptable as a NEW input on
            # the next run — scanned, paid another generation for, or fed to
            # the delete gate. os.replace does not care about the extension.
            it["write"] = it["final"].parent / f"{uuid.uuid4().hex}_{it['final'].name}.tmp"
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

    # The wrapper's manifest recap pops these exact labels for its deletion
    # panel; "deleted sources"/"kept sources" were invisible there.
    extras = {"recompressed": ok, "copied (policy/not smaller)": copied,
              "Sources deleted": _delete_stats["deleted"],
              "Sources deleted (already archived)": _delete_stats["deleted_archived"],
              "Sources KEPT by a delete gate": _delete_stats["kept"],
              "Refused (output belongs to another source)": len(provenance_refused)}
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
