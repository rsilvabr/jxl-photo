#!/usr/bin/env python3
"""
jxl_recompressor.py — Batch JPEG XL -> JPEG XL recompressor (smaller archives, same metadata)

Re-encodes JXL files at a new distance/effort with `cjxl in.jxl out.jxl`, keeping
everything this toolkit stamps into an archive: the base64 ICC (jxlphoto-iccprofile),
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
import contextlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time
from typing import Any, Dict, Optional
import argparse

import numpy as np

# Who wrote a file: recorded in every output's xmpMM:History and printed by
# --version. NOT a user setting. Bumped in ALL FIVE scripts on every release,
# even when only one script changed — tests/test_toolkit_version.py pins the
# five to each other and to the newest section of docs/version_history.md.
TOOLKIT_NAME = "jxl-photo"
TOOLKIT_VERSION = "2.28.0"
TOOLKIT_URL = "https://github.com/rsilvabr/jxl-photo"


def _import_common():
    """jxl_photo_common.py from THIS script's folder, never another copy found
    on sys.path (a test or a pre-fix proof loads a script from another folder;
    the exe also carries a frozen copy). Refused when its version differs."""
    import importlib.util
    here = Path(__file__).resolve().parent
    for key in ("jxl_photo_common", f"jxl_photo_common@{here}"):
        mod = sys.modules.get(key)
        where = getattr(mod, "__file__", None)
        if where and Path(where).resolve().parent == here:
            break
    else:
        path = here / "jxl_photo_common.py"
        if not path.is_file():
            raise ImportError(f"jxl_photo_common.py is missing next to {Path(__file__).name}: "
                              "copy every file of the release into the same folder")
        key = ("jxl_photo_common" if "jxl_photo_common" not in sys.modules
               else f"jxl_photo_common@{here}")
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    if getattr(mod, "TOOLKIT_VERSION", None) != TOOLKIT_VERSION:
        raise ImportError(f"jxl_photo_common.py is version {getattr(mod, 'TOOLKIT_VERSION', None)} "
                          f"and {Path(__file__).name} is {TOOLKIT_VERSION}: copy every file of the "
                          "release together")
    return mod


# The helpers the scripts share live in jxl_photo_common.py, next to this file
# (AGENTS.md, "The common module"). _G is this script's namespace, which the
# shared helpers that read a setting, the logger or the run state are given.
_common = _import_common()
_G = _common.Script(globals())

# Where a provenance refusal sends the user (docs/behavior_and_limitations.md).
GUARD_DOC_URL = (f"{TOOLKIT_URL}/blob/main/docs/behavior_and_limitations.md"
                 f"#renamed-folders-and-the-overwrite-guard")

# MIME types named in the xmpMM:History entry (_history_args).
_HISTORY_MIME = _common._HISTORY_MIME

# Module-level logger; setup_logger() replaces it with a configured instance when main() runs.
logger = logging.getLogger("jxl_recompress")


def _verify_jxl_integrity(*a, **k): return _common._verify_jxl_integrity(_G, *a, **k)


_is_relative_to = _common._is_relative_to


_replace_suffix_token = _common._replace_suffix_token


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


# (size, mtime_ns) of every source as this run was about to READ it, keyed by
# normcased absolute path. The delete gate runs only after the WHOLE pool has
# drained — hours, on a big batch — and a source re-exported in that window
# (an editor exporting into the tree, a scheduled run with Capture One open)
# was deleted without its new version ever being read: the 2026-10-08 audit
# reproduced it. Recorded when the work is handed to the pool; a source
# changed between that and the read is kept too, which only errs on the safe
# side. Cleared at the start of every run.
_source_identity = {}


def _record_source_identity(*a, **k): return _common._record_source_identity(_G, *a, **k)


def _source_changed_since_read(*a, **k): return _common._source_changed_since_read(_G, *a, **k)


_changed_since = _common._changed_since


# Why an in-place replacement was refused (process_group). Logged and kept as
# the file's error detail; the next run re-encodes the new version.
_CHANGED_IN_PLACE = ("the source changed while it was being re-encoded (another "
                     "program or run wrote it) — replacing it would overwrite "
                     "that newer version with a re-encode of the old one")


def _discard_in_place_temp(it) -> None:
    """Remove the verified re-encode of an in-place item that will NOT replace
    its source. It sits in staging or beside the source under a .tmp name;
    the source itself is never touched here."""
    try:
        it["write"].unlink()
    except OSError:
        pass


# What each planned output looked like when this run PLANNED it, keyed by its
# normcased absolute path: None = nothing there, (size, mtime_ns) = that file,
# False = could not be read. The overwrite guards judge an output once, at
# planning, and the swap comes later (hours, on a big batch): another run
# writing the same folder in between had its fresh archive replaced AFTER it
# verified it and deleted its source (#615, review 261110). Recorded right
# BEFORE the judgment, checked right before every swap. A path this run never
# recorded (a direct caller, a test) is not checked. Cleared per run.
_planned_outputs = {}


def _record_planned_outputs(*a, **k): return _common._record_planned_outputs(_G, *a, **k)


def _output_changed_since_planned(*a, **k): return _common._output_changed_since_planned(_G, *a, **k)


def _swap_into_place(*a, **k): return _common._swap_into_place(_G, *a, **k)


def _flush_to_disk(*a, **k): return _common._flush_to_disk(_G, *a, **k)


# The (size, mtime_ns) of each output right after THIS run put it at its final
# path, keyed by source (#618, review 261110 item 4.2). The delete gate trusted
# "promoted this run" + integrity: an output another run replaced in between
# (a valid JXL of another photo) certified the deletion. A source this run
# never recorded (a direct caller, a test) is not checked. Cleared per run.
_promoted_identity = {}


def _note_promoted(src_str, final_path) -> None:
    try:
        st = os.stat(final_path)
    except OSError:
        _promoted_identity[src_str] = False
    else:
        _promoted_identity[src_str] = (st.st_size, st.st_mtime_ns)


def _changed_since_promoted(src_str, final_path):
    """Why the output at `final_path` is no longer the one this run wrote for
    `src_str`, or None."""
    rec = _promoted_identity.get(src_str)
    if rec is None:
        return None
    if rec is False:
        return "the output could not be read right after this run wrote it"
    try:
        st = os.stat(final_path)
    except OSError as e:
        return f"the output could not be checked ({e})"
    if (st.st_size, st.st_mtime_ns) != rec:
        return ("the output changed after this run wrote it (another run "
                "replaced it?)")
    return None


def _reset_abort(*a, **k): return _common._reset_abort(_G, *a, **k)


def _aborted(*a, **k): return _common._aborted(_G, *a, **k)


def _signal_abort(*a, **k): return _common._signal_abort(_G, *a, **k)


def _abort_if_disk_full(*a, **k): return _common._abort_if_disk_full(_G, *a, **k)


def _promote_from_staging(*a, **k): return _common._promote_from_staging(_G, *a, **k)


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


def _scan_state(*a, **k): return _common._scan_state(_G, *a, **k)


def _scan_tick(*a, **k): return _common._scan_tick(_G, *a, **k)


def _batch_read_state(*a, **k): return _common._batch_read_state(_G, *a, **k)


def _batch_read_tick(*a, **k): return _common._batch_read_tick(_G, *a, **k)


def _scan_done(*a, **k): return _common._scan_done(_G, *a, **k)


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
_STAGING_PREFIX_RE = _common._STAGING_PREFIX_RE
# A file still being written belongs to a run in flight, possibly a CONCURRENT
# one sharing this directory. Only sweep what has been sitting still a while.
_STAGING_MIN_AGE_SECONDS = 3600


def _fmt_secs(s):
    """Seconds as "42s" or "7m46s" for the planning timings."""
    s = int(round(s))
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def _fmt_eta(s):
    """A remaining-time estimate as "40s" or "6m": whole minutes above one,
    so an extrapolation from the rate so far does not look exact."""
    s = max(0, s)
    return f"{int(round(s))}s" if s < 60 else f"{int(round(s / 60))}m"


_fmt_size = _common._fmt_size


_staging_leftovers = _common._staging_leftovers


def _report_staging_leftovers(*a, **k): return _common._report_staging_leftovers(_G, *a, **k)


# --clean-staging of one run must not sweep the outputs ANOTHER run left
# waiting in a shared staging folder for their destination folder to finish
# (#578). Every run that writes into staging holds a locked
# `.jxlphoto-live-<id>` file there while it does; the sweep does nothing while
# a live process holds one. The name starts with a dot, so it never matches
# _STAGING_PREFIX_RE: it is never reported or swept as a leftover.
_STAGING_LIVE_PREFIX = ".jxlphoto-live-"
_STAGING_HOLDS = {}
# A live file this young counts as held even when it is not locked: its run
# may be between creating and locking it (261104 review, S3).
_STAGING_LIVE_GRACE_SECONDS = 60


_try_lock_nb = _common._try_lock_nb


_unlock_nb = _common._unlock_nb


def _staging_hold(*a, **k): return _common._staging_hold(_G, *a, **k)


def _holds_staging(*a, **k): return _common._holds_staging(_G, *a, **k)


def _staging_used_by_another_run(*a, **k): return _common._staging_used_by_another_run(_G, *a, **k)


def _clean_staging(*a, **k): return _common._clean_staging(_G, *a, **k)


_marker_matches = _common._marker_matches



_marker_anchor_index = _common._marker_anchor_index

_validate_export_folder_name = _common._validate_export_folder_name


# ExifTool detection - try multiple name variants
_exiftool_cmd = None
def _get_exiftool_cmd(*a, **k): return _common._get_exiftool_cmd(_G, *a, **k)

# cjxl detection - early exit with a clear message if the encoder is missing
_cjxl_cmd = None
def _get_cjxl_cmd(*a, **k): return _common._get_cjxl_cmd(_G, *a, **k)

_tool_version = _common._tool_version

_tool_at_least = _common._tool_at_least


# ─────────────────────────────────────────────
# USER SETTINGS - GENERAL
# ─────────────────────────────────────────────

CJXL_DISTANCE = 1.0
# Target distance for the NEW files (0-25). This is the whole point of the
# tool: archives were written at high quality (d=0.05-0.1) and are recompressed
# smaller later when storage runs short.
# 0   = lossless re-encode (only useful from a LOSSLESS source, see below)
# 1.0 = "visually lossless" (~8MB from a 45MP/16-bit original) — typical target
# 2.0 = smaller still, still very good for screen viewing

CJXL_EFFORT = 7
# Compression effort (1-10). Controls file size and encode time, NOT quality.
# 7 is the sweet spot for camera photos.

CJXL_BUFFERING = None
# [libjxl >= 0.12 only] --buffering flag passed to cjxl. None = cjxl chooses:
# streaming, except at effort 7 with distance >= 3, effort 8-9 with distance
# > 0.5 and effort 10+ (whole image at once, 2.5-8x the RAM). 1 = always
# stream: at effort 7 ~1.5% larger files, same quality; at effort 8-9 the file
# effort 7 would give (cjxl 0.12, measured: docs/README_jxl_recompressor.md,
# "Streaming vs whole-image"). See WORKER_MEMORY_FRACTION.

CJXL_TIMEOUT = 900
# Per-file cjxl/djxl timeout in seconds.

EXIFTOOL_TIMEOUT = CJXL_TIMEOUT
# Timeout (seconds) for each per-file exiftool call (metadata read/copy,
# markers). Same patience as the codec: with many workers on a busy hard disk
# a metadata rewrite can stall for minutes, and the old 15-180 s limits turned
# good files into errors (2026-10-06: twelve exiftool timeouts in one run, all
# reported as a codec timeout). The planning-time batch reads use the two
# EXIFTOOL_BATCH_* settings below instead.

# Planning-time exiftool BATCH reads (markers of up to 400 files per call).
# A batch is stopped only when exiftool has printed nothing for
# EXIFTOOL_BATCH_STALL seconds (a hang), or after EXIFTOOL_BATCH_TIMEOUT
# seconds in all. A slow disk keeps printing and is left to finish: the
# old fixed 180 s per batch lost every batch of a 2025 library on a busy
# disk (#537).
EXIFTOOL_BATCH_STALL = 180
EXIFTOOL_BATCH_TIMEOUT = 1800

WORKER_MEMORY_FRACTION = 1.0
# Caps the memory the parallel encodes may take. The peak of one cjxl is
# estimated from the largest image in the batch and the encode settings: cjxl
# encodes the whole image at once (2.5-8x the memory of its usual streaming
# encode) at effort 7 with distance >= 3, effort 8-9 with distance > 0.5,
# effort 10+, or --buffering 0. At most as many cjxl as fit run at once, and
# the memory left over pays for extra workers that decode and convert the
# next files meanwhile. The whole run stays within this fraction of the memory
# WORKER_MEMORY_LIMIT picks (by default the smaller of what the system can
# still commit and the physical RAM still available). 0 disables the cap.
# 1.0 by default (the TIFF encoder keeps 0.8): the per-cjxl estimate already
# sits ~10% above the measured peak, and every other step of a file is
# budgeted on its own.

WORKER_MEMORY_LIMIT = "both"
# What the --workers cap budgets from (WORKER_MEMORY_FRACTION of it):
# "both"     -> the smaller of the two below (default).
# "commit"   -> memory the system can still commit (RAM + pagefile). The hard
#               limit: past it cjxl fails (WinError 1455). Alone, with a large
#               pagefile, it can allow more workers than the RAM holds, and the
#               run then pages to disk.
# "physical" -> physical RAM still available (free + standby cache). Alone it
#               ignores the commit limit: with a small pagefile and programs
#               that reserve more than they use (browsers), cjxl can fail.
# On Linux both read MemAvailable.

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

ON_SCAN = "copy"
# What to do when a FILM SCAN (below) would be re-encoded LOSSY — whatever its
# source distance. Same values as ON_DOWNGRADE. Default copies the file
# verbatim: a scan can only be redone by scanning again, and the heavy
# processing a raw negative gets later (inversion, curves) magnifies a loss
# that is invisible in the file. A lossless request, and derivatives
# (--output-icc, --resize-*, --sharpen), are never affected. Independent of
# the other policies: the more conservative action wins.

SCAN_SCOPE = "all"
# Which scans ON_SCAN protects: "all" (default) or "raw" — raw scans only
# (the encoder's jxlphoto-scan:raw; a scan with no such marker counts as raw).

SCANNER_SOFTWARE = (
    "SilverFast",
    "VueScan",
    "Epson Scan",
    "Nikon Scan",
    "FlexColor",
    "Frontier",
    "EZ Controller",
    "ScanGear",
    "Plustek",
)
# A JXL is a film scan when the encoder marked it (jxlphoto-scan:raw/final),
# when it is the IR page of a split scan (jxlphoto-subfiletype:4), when its
# Software tag CONTAINS one of these (case-insensitive), or when it was made
# from a TIFF (jxlphoto-origin:tiff8/tiff16) that had neither a Software tag
# nor camera EXIF (lab scans, Epson Scan) — the last three cover masters
# archived before the marker existed. Same list as the encoder's. Never one
# the encoder marked jxlphoto-scan:no (a camera TIFF that had lost its tags).

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

EXPLORER_THUMBNAIL = "auto"
# A small sRGB JPEG of the image stored inside the output JXL, which jxl-thumb
# (https://github.com/rsilvabr/jxl-thumb, 0.2.0+) shows in Windows Explorer
# instead of decoding the photo. Also --explorer-thumbnail.
# "auto" (default) -> every output when jxl-thumb is installed for this
#          Windows user, lossless outputs (--distance 0) only otherwise: a
#          lossless JXL has no small preview inside, so a thumbnail otherwise
#          means decoding the whole file. Lossy files keep a 1:8 preview.
# "on"   -> every output. "off" -> none.
# A re-encode never inherits the source's (the metadata copy does not carry
# the box); a verbatim copy keeps it, still valid.
# --refresh-explorer-thumbnails writes, renews or (with "off") removes them in
# EXISTING JXLs in place, keeping each file's modified date.
# Needs djxl and ImageMagick; without them the output is written without it.

EXPLORER_THUMBNAIL_SIZE = 768
# Long side in pixels. 768 is the largest thumbnail Explorer asks for its icon
# views (extra large icons); bigger requests (preview pane) use the JXL itself.

EXPLORER_THUMBNAIL_MAX_SHARE = 0.10
# Small files get none: an image no larger than EXPLORER_THUMBNAIL_SIZE, or a
# thumbnail that would be more than this share of the file (an 800 px d=3 JXL
# of 50 KB, a 2048 px derivative). Those decode fast anyway. A 45 MP master's
# thumbnail is 0.05-1% of it.

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

REDERIVE_ON_ENCODE_CHANGE = True
# Derivatives only (--output-icc / --resize-* / --sharpen). Each derivative
# records its recipe AND the distance/effort it was encoded with
# (jxlphoto-derived:sRGB/d4e9). True -> a sync run re-derives an existing
# derivative whose recorded distance/effort differ from this run's, exactly as
# it already does when the colour/size/sharpening recipe changes.
# False -> only a recipe change re-derives; new distance/effort apply to new
# files only. Derivatives written before v2.7.0 record no distance/effort and
# are never re-derived for it (run once with --overwrite to refresh them).
# Can also be set via --rederive-on-encode-change / --no-rederive-on-encode-change.

REDERIVE_ON_LOWER_EFFORT = False
# Derivatives only, and only while REDERIVE_ON_ENCODE_CHANGE is True: what to
# do when this run asks for the SAME distance as the existing derivative but a
# LOWER effort. At one distance cjxl aims at the same quality; a higher effort
# only buys a smaller file. So:
# False -> keep the existing derivative (default): re-encoding it would cost
#          CPU time and return a larger file. A higher effort still re-derives.
# True  -> re-derive on any distance/effort change, in either direction.
# A distance change always re-derives (a lower distance is a larger, finer
# file: neither direction is "better").
# Streamed encodes carry an "s" in the record (sRGB/d3e9s). cjxl 0.12 streams
# below effort 7, at effort 7 under distance 3, at effort 8-9 up to distance
# 0.5, and always with --buffering 1-3; a streamed lossy effort 8-9 encode is
# effort 7's file (measured, see CJXL_BUFFERING). So e9s ranks as e7s, below a
# whole-image e7, and a whole-image e9 replaces it. Records without the "s"
# (whole image, or written before the mark existed) read as whole-image.
# Can also be set via --rederive-on-lower-effort / --no-rederive-on-lower-effort.

ENCODE_TAG_MODE = "history"
# The NEW encoding parameters are APPENDED to the chain (gen= reconciled at
# its head) in the output's xmpMM:History entry, mirroring jxl_tiff_encoder.py.
# This setting only decides where a VISIBLE copy goes:
# "history"  -> nowhere else (default)
# "comment"  -> also XMP exif:UserComment (Windows Explorer's "Comments")
# "title"    -> also XMP-dc:Description (Explorer's Title; before v2.24.0 the
#               only place; unrelated text is kept first)
# "software" -> also the EXIF Software field
# "xmp"      -> the old default's name: same as "history"
# "off"      -> record nothing (the History entry says "no encode record");
#               any previous record is STRIPPED, so a later run never trusts
#               stale parameters. The only way to deliberately discard the
#               lineage.
# A source written before v2.24.0 keeps its chain in dc:Description or
# Software: it is read there, and the output gets it in the History.

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
# Every run refuses to OVERWRITE an existing output whose markers name a
# different origin (a markerless one is overwritten as before); with
# --delete-source in a collapsing mode every existing output must match.
# There is no "adopt" here: recompressor outputs keep the source JXL's own
# markers verbatim, so an archive made by jxl_tiff_encoder.py is provable as-is.

OVERWRITE_GUARD = True
# Refuse to OVERWRITE an existing output whose jxlphoto-src marker names a
# DIFFERENT source (every run, every mode — #496-#499). That protects the
# only copy of a photo when an earlier run deleted its source and a different
# photo with the same name now lands on its output.
# The marker records the source's full PATH, so a renamed or moved folder
# reads as "a different source" too: re-exporting a photo after renaming its
# folder is refused. If you never delete sources with this toolkit and you
# rename folders, set False (or pass --no-overwrite-guard). Never combined
# with --delete-source: that run exits 2. Still refused with the guard off:
# a lossless JPEG archive (jbrd) of another JPEG, a TIFF that is an original
# master or was edited (decoder), a file that is not one of a derivative
# run's own outputs. Details: docs/behavior_and_limitations.md, "Renamed
# folders and the overwrite guard".

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

# ─────────────────────────────────────────────
# SCRIPT SETTINGS OVERLAY (JXLPHOTO_SETTINGS)
# ─────────────────────────────────────────────
# The settings above can also be changed WITHOUT editing this file, in a JSON
# file the wrapper writes (Settings -> Script settings):
#     {"jxl_tiff_encoder": {"EXPLORER_THUMBNAIL_SIZE": 512}, ...}
# Where: the path in the JXLPHOTO_SETTINGS environment variable (set but
# empty = no overlay), else .jxl_script_settings.json next to the scripts (a
# portable install), else in the home folder (%USERPROFILE% on Windows).
# Only the names in _OVERLAY_SETTINGS are taken, each checked against its
# type and range; the safety settings (deletion, confirmations, guards,
# verification thresholds, overwrite, the encoder's copy of the original
# ICC) never are. A value it takes IS this
# script's setting for the run (the per-run reset restores it), and every
# run's log names the file and the settings it changed.
_SETTINGS_OVERLAY_FILE = ".jxl_script_settings.json"
_SETTINGS_OVERLAY_ENV = "JXLPHOTO_SETTINGS"
_OVERLAY_SAFETY_SETTINGS = frozenset({
    "DELETE_SOURCE", "DELETE_CONFIRM", "DELETE_SKIPPED", "DELETE_SOURCE_REQUIRE_MD5",
    "STORE_MD5", "OVERWRITE_GUARD", "VERIFY_ROUNDTRIP", "VERIFY_LOSSY_MIN_MEAN_RATIO",
    "VERIFY_LOSSY_MIN_PSNR", "ALLOW_INCOMPLETE_GROUPS", "PROVENANCE_CHECK",
    "ADOPT_SCAN", "OVERWRITE", "STRIP_METADATA", "AUTO_REPAIR_JBRD",
    "EMBED_ICC_IN_JXL"})


def _settings_overlay_path(*a, **k): return _common._settings_overlay_path(_G, *a, **k)


_settings_overlay_section = _common._settings_overlay_section


_overlay_value = _common._overlay_value


def _apply_settings_overlay(*a, **k): return _common._apply_settings_overlay(_G, *a, **k)


def _log_settings_overlay(*a, **k): return _common._log_settings_overlay(_G, *a, **k)

# What the overlay may change in this script: name -> (kind, ...) checked by
# _overlay_value. A setting that is not here keeps the script's value.
_OVERLAY_SETTINGS = {
    "CJXL_DISTANCE": ("float", 0.0, 25.0),
    "CJXL_EFFORT": ("int", 1, 10),
    "CJXL_BUFFERING": ("choice", None, 0, 1, 2, 3),
    "CJXL_TIMEOUT": ("int", 1, None),
    "EXIFTOOL_TIMEOUT": ("int", 1, None),
    "EXIFTOOL_BATCH_STALL": ("int", 1, None),
    "EXIFTOOL_BATCH_TIMEOUT": ("int", 1, None),
    "TEMP_DIR": ("path?",),
    "TEMP2_DIR": ("path?",),
    "WORKER_MEMORY_FRACTION": ("float", 0.0, 2.0),   # 0 = no cap; above 1 trusts the estimate
    "WORKER_MEMORY_LIMIT": ("choice", "both", "commit", "physical"),
    "ON_DOWNGRADE": ("choice", "ask", "copy", "skip", "convert"),
    "ON_REGENERATION": ("choice", "ask", "copy", "skip", "convert"),
    "ON_UNKNOWN": ("choice", "ask", "copy", "skip", "convert"),
    "ON_SCAN": ("choice", "ask", "copy", "skip", "convert"),
    "SCAN_SCOPE": ("choice", "all", "raw"),
    "SCANNER_SOFTWARE": ("strs",),
    "JBRD_POLICY": ("choice", "copy", "skip", "convert"),
    "KEEP_SMALLER": ("bool",),
    "EXPLORER_THUMBNAIL": ("choice", "auto", "on", "off"),
    "EXPLORER_THUMBNAIL_SIZE": ("int", 64, 2048),
    "EXPLORER_THUMBNAIL_MAX_SHARE": ("float", 0.0, 1.0),
    "REDERIVE_ON_ENCODE_CHANGE": ("bool",),
    "REDERIVE_ON_LOWER_EFFORT": ("bool",),
    "ENCODE_TAG_MODE": ("choice", "history", "comment", "title", "xmp", "software", "off"),
    "CONVERTED_JXL_FOLDER": ("name",),
    "JXL_FOLDER_NAME": ("name",),
    "JXL_SUFFIX_TO_REPLACE": ("name",),
    "JXL_SUFFIX_REPLACE": ("name",),
    "EXPORT_MARKER": ("name",),
    "EXPORT_JXL_FOLDER": ("name",),
    "EXPORT_JXL_SUBFOLDER": ("name?",),
}
# Settings computed from another one: they follow it (see _apply_settings_overlay).
_OVERLAY_DERIVED = {"EXIFTOOL_TIMEOUT": "CJXL_TIMEOUT"}
_SETTINGS_OVERLAY = _apply_settings_overlay(globals(), "jxl_recompressor", _OVERLAY_SETTINGS,
                                            _OVERLAY_DERIVED)

SCRIPT_DIR = Path(__file__).parent
# JXLPHOTO_LOG_DIR (environment variable) moves the log folders elsewhere —
# the test suite and the real-photo battery set it so their runs never
# land in Logs\ next to the scripts.
LOG_DIR = (Path(os.environ["JXLPHOTO_LOG_DIR"]) if os.environ.get("JXLPHOTO_LOG_DIR")
           else SCRIPT_DIR / "Logs") / "jxl_recompressor"

counter_lock = threading.Lock()
_counter = {"done": 0, "total": 0}
# What the deletion actually did. Module-level because process_group owns the
# delete gate but main() owns the summary.
_delete_stats = {"deleted": 0, "deleted_archived": 0, "kept": 0}

# Per-file failure reason recorded by convert_one, so the run summary can say
# WHAT failed instead of a bare "error" (the reason used to exist only in the
# per-file log line, and the summary's failures list carried the status word).
_error_details = {}

# Lower-cased fragments of the errors a run out of memory produces (Windows
# commit limit, Python, cjxl, exiftool's Perl). Only used for the end-of-run
# hint. "memoryerror" matches because an empty exception message is recorded
# as its type name (see convert_one / process_group).
_MEMORY_ERROR_SIGNATURES = ("winerror 1455", "memoryerror", "bad_alloc",
                            "cannot allocate memory", "out of memory",
                            "jxlencoderprocessoutput failed",
                            "failed to create image frame",
                            "getting pixel data failed")

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

# Script defaults for every global the CLI mutates, captured at import. main()
# resets run-scoped state UNCONDITIONALLY at entry (a second in-process run used
# to inherit the first one's armed --delete-source / --overwrite / --staging —
# the same class as the encoder's #431), and the reset must restore THESE values
# and not hardcoded literals: a user who edits a setting in the file must not
# have it silently revert at the top of every run. New parameters go last.
_RUN_DEFAULTS = {
    "OVERWRITE": OVERWRITE,
    "DELETE_SOURCE": DELETE_SOURCE,
    "DELETE_CONFIRM": DELETE_CONFIRM,
    "DELETE_SKIPPED": DELETE_SKIPPED,
    "VERIFY_ROUNDTRIP": VERIFY_ROUNDTRIP,
    "KEEP_SMALLER": KEEP_SMALLER,
    "REDERIVE_ON_ENCODE_CHANGE": REDERIVE_ON_ENCODE_CHANGE,
    "ON_DOWNGRADE": ON_DOWNGRADE,
    "ON_REGENERATION": ON_REGENERATION,
    "ON_UNKNOWN": ON_UNKNOWN,
    "ON_SCAN": ON_SCAN,
    "SCAN_SCOPE": SCAN_SCOPE,
    "SCANNER_SOFTWARE": SCANNER_SOFTWARE,
    "JBRD_POLICY": JBRD_POLICY,
    "ENCODE_TAG_MODE": ENCODE_TAG_MODE,
    "CJXL_DISTANCE": CJXL_DISTANCE,
    "CJXL_EFFORT": CJXL_EFFORT,
    "CJXL_BUFFERING": CJXL_BUFFERING,
    "PROVENANCE_CHECK": PROVENANCE_CHECK,
    "OVERWRITE_GUARD": OVERWRITE_GUARD,
    "TEMP2_DIR": TEMP2_DIR,
    "REDERIVE_ON_LOWER_EFFORT": REDERIVE_ON_LOWER_EFFORT,
    # --output-icc only assigns it when the flag is given: without the reset a
    # second in-process run without the flag inherited the first one's target
    # and wrote a colour-converted DERIVATIVE (#529).
    "OUTPUT_ICC": OUTPUT_ICC,
    "EXPLORER_THUMBNAIL": EXPLORER_THUMBNAIL,
}

# XMP dc:Relation provenance markers — the same strings the encoder writes, so
# a recompressed archive stays provable by the DECODER's delete gates.
SRC_PREFIX = "jxlphoto-src:"
SRCSUM_PREFIX = "jxlphoto-srcsum:"
ORIGIN_PREFIX = _common.ORIGIN_PREFIX
# dc:Relation token: the EXIF Orientation (2-8) of the JPEG a JXL was made
# from. A JXL made from a rotated JPEG keeps the pixels as the JPEG stored
# them plus an orientation in its CODESTREAM; a re-encode turns the pixels
# upright and its codestream says 1, so the original value is recorded here,
# once, and carried forward. The transcoder reads it to give a JPEG its
# original orientation back. Never in a jbrd container or a verbatim copy.
ORIENTATION_PREFIX = "jxlphoto-orientation:"
# Film scan marker written by the encoder: jxlphoto-scan:raw / :final.
SCAN_PREFIX = "jxlphoto-scan:"
SUBFILETYPE_PREFIX = "jxlphoto-subfiletype:"
# The encoder's THUMB_XMP_FLAG: this JXL is the THUMBNAIL page of a split TIFF
# (a reduced-resolution preview). A derivative run makes none of it.
THUMB_FLAG = "jxlphoto-thumb"
PAGE_PREFIX = _common.PAGE_PREFIX
# The encoder's PAGE_XMP_PREFIX: which page of its TIFF this JXL holds.
_CAMERA_THUMB_MAX_SIDE = _common._CAMERA_THUMB_MAX_SIDE


_magick_out_path = _common._magick_out_path


_is_camera_thumbnail_page = _common._is_camera_thumbnail_page


# dc:Relation token naming what the FIRST master of this photo was made from:
# tiff8 / tiff16 (encoder), jpeg / png8 / png16 / png (transcoder, pixel
# re-encode), jpeg (recompressor re-encoding a jbrd JXL), jxl-lossy /
# jxl-lossless (recompressor, a JXL no toolkit script wrote, as jxlinfo reads
# its header; plain `jxl` when jxlinfo could not tell, and in files written by
# v2.10.0). Written once by the script that first touches a clean input, then
# carried forward unchanged — absent means unknown, never guessed. The only
# reader is the recompressor's --on-regeneration guard (_hidden_generations).
_TOOLKIT_MARKER_NAMESPACE = _common._TOOLKIT_MARKER_NAMESPACE
# An encode record anywhere in Description/Software: the input came from this
# toolkit (an older version, before the origin marker existed).
_ORIGIN_RECORD_RE = re.compile(r"(?:^|\|)\s*cjxl d=", re.MULTILINE)
# Multi-page group id, written by the encoder into every page's dc:Relation.
# Pages that share a document live or die together: the delete gate removes
# the whole group or nothing (a half-deleted group is spread across two
# folders with a dangling master page).
MULTIPAGE_XMP_MARKER = "jxlphoto-mpg:"
MULTIPAGE_PAGES_PREFIX = "jxlphoto-pages:"
# The encoder's PAGES_XMP_PREFIX: how many JXLs the split produced. The delete
# gate keeps a group whose pages are not all in the run (#574).

DERIVED_XMP_PREFIX = "jxlphoto-derived:"
# dc:Relation token on every --output-icc output: "<prefix><label>", label =
# sRGB / AdobeRGB / icc-<md5[:12]>. Marks the file as a colour-converted
# derivative (NOT an archive of the original) and records its target, so a
# run with a different target re-derives instead of trusting the old file.
ICC_PROFILE_PREFIX = _common.ICC_PROFILE_PREFIX
# The encoder's: the ORIGINAL ICC profile, byte for byte, as
# "jxlphoto-iccprofile:<base64>" in dc:Relation. Files written up to v2.18.x
# carry it in XMP CreatorTool as "ICC:<base64>"; both are read (_carried_icc),
# and an output written from such a file moves it to the token
# (_legacy_icc_lines). A --output-icc derivative carries the TARGET profile.
ICC_INHERITED_XMP_FLAG = "jxlphoto-icc:inherited"
# Written by the encoder on pages that inherit IFD0's ICC. Meaningless on a
# derivative (it carries its own converted profile) — removed there.

# The encoder's encoding-parameters tag: "cjxl d=0.1 e=7", possibly several in
# a " | "-separated chain (the LAST one is the current file's).
_ENCODE_TAG_RE = _common._ENCODE_TAG_RE

# The stored generation token, read ONLY as a complete " | "-delimited segment
# (or bounded by the field's start/end): "Project gen=3 phase 2" is running
# text, not a token, and must never be read as one.
_GEN_TAG_RE = _common._GEN_TAG_RE

# The whole machine block as ONE unit: an optional gen= lead segment followed
# by the contiguous cjxl d=/e= chain. The block only starts at a segment
# boundary ((?:^|\|)), so it never bites into a caption's running text; and
# matching only the first cjxl entry would leave an orphaned gen= and a
# partial chain behind. Legacy fields (a chain with no gen=) are the normal
# case and match in full.
_MACHINE_BLOCK_RE = _common._MACHINE_BLOCK_RE

_MIN_EFFECTIVE_DISTANCE = _common._MIN_EFFECTIVE_DISTANCE
# cjxl >= 0.12 clamps every lossy distance below this to the same value:
# --distance 0.005 ... 0.05 were measured producing BYTE-IDENTICAL output
# (VarDCT and lossy modular alike). libjxl PR #4238 set the floor so the DC
# coefficients stay inside int16 — below it the bitstream leaves Level 5 of
# the JPEG XL spec.
_MIN_EFFECTIVE_DISTANCE_PRE_012 = _common._MIN_EFFECTIVE_DISTANCE_PRE_012
# cjxl 0.11.2 (measured, 2026-09-24): only 0.005 and 0.01 were identical;
# 0.02/0.03/0.04 were real steps above 0.05 (d=0.01: +8.7 dB PSNR at 1.75x
# the size of d=0.05). Older builds share the pre-#4238 code.


_min_effective_distance = _common._min_effective_distance


def _warn_distance_clamp(*a, **k): return _common._warn_distance_clamp(_G, *a, **k)


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


# Peak private memory of ONE cjxl, in bytes per pixel of the image, measured on
# libjxl 0.12.0 with 45 MP 16-bit photos (+~10% margin). Plan
# 261004_Claude_plan_memoria-workers.md has the full table.
_STREAMING_BYTES_PER_PIXEL = _common._STREAMING_BYTES_PER_PIXEL
_WHOLE_IMAGE_E7_BYTES_PER_PIXEL = _common._WHOLE_IMAGE_E7_BYTES_PER_PIXEL
_WHOLE_IMAGE_E8_BYTES_PER_PIXEL = _common._WHOLE_IMAGE_E8_BYTES_PER_PIXEL
_MODULAR_LOSSY_BYTES_PER_PIXEL = _common._MODULAR_LOSSY_BYTES_PER_PIXEL
_UNKNOWN_IMAGE_PIXELS = 60_000_000

# Peak private memory, in bytes per pixel, of the steps a recompressor worker
# runs on a file besides the full-size cjxl: djxl to a 16-bit PNG (19 measured)
# and the magick recipe (36 with Lab + unsharp + resize), plan
# 261004_Claude_plan_memoria-workers.md, +~10% margin. One budget covers any
# of them: a worker runs one at a time.
_SIDE_STEP_BYTES_PER_PIXEL = 40

# How many full-size cjxl encodes may run at once (_run_cjxl_full); None = no
# limit. Set by main() from _memory_capped_pipeline for the length of the
# pool, then cleared.
_CJXL_SLOTS_N = None
_CJXL_SEMAPHORE = None


def _set_cjxl_slots(n):
    """Limit the full-size cjxl encodes to `n` at a time (None/0 = no limit)."""
    global _CJXL_SLOTS_N, _CJXL_SEMAPHORE
    _CJXL_SLOTS_N = n or None
    _CJXL_SEMAPHORE = threading.BoundedSemaphore(n) if n else None


def _run_cjxl_full(cmd):
    """_run_captured for a full-size cjxl encode, holding one of the cjxl slots
    while it runs (see _memory_capped_pipeline). The wait happens before the
    process starts, so CJXL_TIMEOUT never counts it."""
    sem = _CJXL_SEMAPHORE
    if sem is None:
        return _run_captured(cmd, CJXL_TIMEOUT)
    sem.acquire()
    try:
        return _run_captured(cmd, CJXL_TIMEOUT)
    finally:
        sem.release()


_cjxl_whole_image = _common._cjxl_whole_image


_cjxl_bytes_per_pixel = _common._cjxl_bytes_per_pixel


_windows_memory_status = _common._windows_memory_status


_linux_mem_available = _common._linux_mem_available


_available_commit_bytes = _common._available_commit_bytes


_available_physical_bytes = _common._available_physical_bytes


_MEMORY_LIMITS = ("both", "commit", "physical")
# How the cap's log and warning name the budget it used.
_MEMORY_BUDGET_TEXT = {"commit": "the system can still commit (RAM + pagefile)",
                       "physical": "of physical RAM still available"}


def _available_memory_bytes(*a, **k): return _common._available_memory_bytes(_G, *a, **k)


def _memory_capped_workers(*a, **k): return _common._memory_capped_workers(_G, *a, **k)


def _largest_encode_pixels(items):
    """The largest image cjxl will ENCODE this run, or None when the source
    size is the answer: only a resized derivative encodes fewer pixels than
    it decodes. An item whose dimensions are unknown counts at its full size
    (fail closed: a smaller estimate could oversubscribe the memory)."""
    if not (DERIVATIVE and RESIZE_MODE):
        return None
    largest = 0
    for it in items:
        w, h = it.get("dims") or (0, 0)
        if not (w and h):
            return None
        geom = _resize_geometry(w, h, RESIZE_MODE, RESIZE_VALUE, ALLOW_UPSCALE)
        largest = max(largest, geom[0] * geom[1] if geom else w * h)
    return largest or None


def _memory_capped_pipeline(requested, max_pixels, distance, effort,
                            buffering=None, cjxl_pixels=None):
    """(workers, cjxl_slots) for the recompressor's pool.

    The peak that matters is cjxl's; the other steps of a file (djxl, the
    magick recipe, exiftool, the checks) need a fraction of it. So at most
    `cjxl_slots` full-size encodes run at once (_run_cjxl_full) and the memory
    left over goes to extra workers that decode and convert the next files
    meanwhile — a slot no longer sits idle while its worker is busy with the
    other steps. Worst case = every slot at its cjxl peak plus every extra
    worker at its side-step peak, within WORKER_MEMORY_FRACTION of the budget
    (_available_memory_bytes). cjxl_slots is None when nothing is capped
    (fraction 0, memory unknown).

    `cjxl_pixels` is the largest image cjxl will really ENCODE when that is
    smaller than the source (a resized derivative: a 2048 px copy of a 45 MP
    master is ~3 MP); the side steps still decode the full `max_pixels`.
    """
    if WORKER_MEMORY_FRACTION <= 0:
        return requested, None
    if WORKER_MEMORY_LIMIT not in _MEMORY_LIMITS:
        logger.warning(f"WORKER_MEMORY_LIMIT = {WORKER_MEMORY_LIMIT!r} is not one of "
                       f"{', '.join(_MEMORY_LIMITS)} — using 'both'")
    pixels = max_pixels if max_pixels > 0 else _UNKNOWN_IMAGE_PIXELS
    enc_pixels = (min(cjxl_pixels, pixels) if cjxl_pixels and cjxl_pixels > 0
                  else pixels)
    per_cjxl = enc_pixels * _cjxl_bytes_per_pixel(distance, effort, buffering)
    per_side = pixels * _SIDE_STEP_BYTES_PER_PIXEL
    avail, budget = _available_memory_bytes()
    if avail is None:
        logger.info("Memory: could not read the available memory — "
                    "--workers not capped")
        return requested, None
    total = avail * WORKER_MEMORY_FRACTION
    slots = max(1, int(total // max(per_cjxl, per_side)))
    extra = 0
    if per_cjxl > per_side and not VERIFY_ROUNDTRIP:
        # --verify-roundtrip decodes both images inside Python (not measured):
        # no extra workers then, exactly the pre-pipeline cap.
        extra = max(0, min(slots, int((total - slots * per_cjxl) // per_side)))
    workers = min(requested, slots + extra)
    slots = min(slots, workers)
    if _cjxl_whole_image(distance, effort, buffering):
        kind = "whole-image"
    else:
        kind = "streaming"
    logger.info(f"Memory: ~{per_cjxl / 2**30:.1f} GB per cjxl ({kind} encode, "
                f"{enc_pixels / 1e6:.0f} MP, d={distance} e={effort}), "
                f"~{per_side / 2**30:.1f} GB per file being prepared | "
                f"{avail / 2**30:.1f} GB available ({budget}) | "
                f"workers {workers}, cjxl at a time {slots}")
    if workers < requested:
        hint = ""
        if kind == "whole-image" and effort >= 8:
            hint = (" This distance/effort makes cjxl encode the whole image at "
                    "once. --buffering 1 would stream it, but a streamed effort "
                    "8+ encode is effort 7's file (~8% larger): for a light run "
                    "use effort 7 instead.")
        elif kind == "whole-image":
            hint = (" This distance/effort makes cjxl encode the whole image at "
                    "once; --buffering 1 keeps it streaming (~3x less memory, "
                    "files ~1.5% larger, same quality).")
        more = (f"; {workers - slots} more worker(s) decode and convert the next "
                f"files meanwhile" if workers > slots else "")
        logger.warning(f"--workers {requested} reduced to {workers}: {slots} cjxl "
                       f"process(es) at ~{per_cjxl / 2**30:.1f} GB each fit in the "
                       f"{avail / 2**30:.1f} GB {_MEMORY_BUDGET_TEXT[budget]} "
                       f"(WORKER_MEMORY_FRACTION={WORKER_MEMORY_FRACTION}, "
                       f"WORKER_MEMORY_LIMIT={WORKER_MEMORY_LIMIT}){more}.{hint}")
    return workers, slots


# Leftover temps of an interrupted run (#546). Every output is written to a
# '<uuid>_<name>.tmp' beside it (or rewritten through one) and renamed into
# place; a run killed in between (an idle-timeout kill, Ctrl+C, a power cut)
# leaves that temp behind. Its .tmp suffix keeps every scan from adopting it,
# but nothing removed it either, and it can be a complete master's size. Each
# run removes, right after planning, the leftover temps of the outputs it is
# about to write: those cannot belong to a live run (two writers on one output
# is what the duplicate-output abort exists to stop). The temps of any other
# output, and files without the 32-hex-digit prefix, are never touched.
_ORPHAN_TEMP_RE = re.compile(r"^(?:[0-9a-f]{32}_)+(?:repair_)?(.+?)(?:\.tmp)+$")


def _sweep_orphan_temps(*a, **k): return _common._sweep_orphan_temps(_G, *a, **k)


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
    logger.info(f"{TOOLKIT_NAME} {TOOLKIT_VERSION} | {Path(__file__).name}")
    _log_settings_overlay()
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


def _log_rejected_file(*a, **k): return _common._log_rejected_file(_G, *a, **k)


def next_count(*a, **k): return _common.next_count(_G, *a, **k)


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

has_jbrd_box = _common.has_jbrd_box


md5_of_file = _common.md5_of_file


# ---------------------------------------------------------------------------
# Provenance (which source made an output). Same markers as the encoder's —
# the recompressor copies them verbatim, so they still point at the ORIGINAL
# source (the TIFF), which is exactly what the decoder's delete gates compare.
# ---------------------------------------------------------------------------

_COLLAPSING_MODES = _common._COLLAPSING_MODES


_run_collapses_structure = _common._run_collapses_structure


_source_path_id = _common._source_path_id


# {(normcased path, size, mtime_ns): sha256 digest} for the run. A multi-page
# TIFF is converted one PAGE at a time and each page asked for its source's
# content id, so a 700 MB three-page scan was read 2.1 GB worth of times to
# produce the same hash three times over.
#
# Keyed on size and mtime as well as the path, so a source edited mid-run is
# never served a stale hash. No lock: two threads racing compute the SAME value
# and the second store is a no-op.
_content_id_cache = {}


def _file_digest_cached(*a, **k): return _common._file_digest_cached(_G, *a, **k)


def _file_content_id(*a, **k): return _common._file_content_id(_G, *a, **k)


def _read_source_markers_batch(*a, **k): return _common._read_source_markers_batch(_G, *a, **k)


def _drop_derivative_inputs(files):
    """`files` minus the JXLs that carry a jxlphoto-derived marker — modes 6/7
    without an explicit subfolder (the 261103 review of delivery A, S1/S2). A
    derivative next to its master shares the master's name and lands on the
    same output, whatever its folder is called: even a name only the run that
    made it knew (--export-jxl-folder 16B_JXL_sRGB), which the folder skip
    (_skip_other_tool_output) cannot know. A file whose marker cannot be read
    is kept: the duplicate-output check still guards it."""
    if not files:
        return files
    labels = _read_derived_markers_batch(list(files))
    kept = [f for f in files if not labels.get(str(f))]
    _n = len(files) - len(kept)
    if _n:
        logger.info(f"Ignored {_n} derivative JXL(s) below the export marker (they carry a jxlphoto-derived marker: copies made from a master, not masters) — to process them, run mode 7 with --export-subfolder set to their folder")
    return kept


def _read_derived_markers_batch(*a, **k): return _common._read_derived_markers_batch(_G, *a, **k)


def _markers_match(out_info: dict, src_info: dict, mode_check: str = "path") -> bool:
    """Does the existing output record the SAME origin as the source JXL in
    front of it?

    Both sides carry the encoder's jxlphoto-src/srcsum pair pointing at the
    ORIGINAL source (the TIFF), so equality means "same photo": the recompressor
    copies them verbatim, and an earlier recompress of this same source stamped
    the same ids.

    mode_check mirrors the other scripts' --provenance: "path" requires the
    recorded LOCATION (jxlphoto-src) to agree — the strict, documented default —
    while "content" ALSO accepts a matching source-BYTES id (jxlphoto-srcsum),
    which survives a moved folder. The flag used to be assigned and never read,
    so path runs quietly accepted content-only matches.

    "content" is a SUPERSET of "path", exactly like _provenance_ok in the other
    scripts and like the help says ("also accepts"): it used to compare the
    srcsum ONLY, so a master re-exported in place (same path, new bytes) was
    refused with content and accepted with path.
    """
    if (out_info.get("src") and src_info.get("src")
            and out_info["src"] == src_info["src"]):
        return True
    if mode_check == "content":
        return bool(out_info.get("srcsum") and src_info.get("srcsum")
                    and out_info["srcsum"] == src_info["srcsum"])
    return False


def _markers_prove_archive(out_info: dict, src_info: dict,
                           mode_check: str = "path") -> bool:
    """The DELETE-side proof for a SKIPPED source: `_markers_match` (the
    overwrite guard's question, where the recorded location is enough) AND the
    same recorded source-bytes id on both sides.

    A master re-encoded from a CHANGED TIFF keeps the TIFF's path but carries
    a new srcsum. If it lands with an older date than the existing output
    (a restored backup), smart sync skips it, and the path alone let the
    output of the OLD master delete the new one. An output or a source
    without a srcsum proves nothing: fail closed.
    """
    if not _markers_match(out_info, src_info, mode_check):
        return False
    return bool(out_info.get("srcsum") and src_info.get("srcsum")
                and out_info["srcsum"] == src_info["srcsum"])


_argfile_safe = _common._argfile_safe


_struct_escape = _common._struct_escape


_history_mime = _common._history_mime


def _history_args(*a, **k): return _common._history_args(_G, *a, **k)


def _guard_refusal_hint(*a, **k): return _common._guard_refusal_hint(_G, *a, **k)


# Charset directives for exiftool argfiles:
# - FileName=UTF8: file paths in the argfile are UTF-8 (Windows default is the
#   system codepage, so non-ASCII paths would not be found).
# - UTF8: tag VALUES read/written are UTF-8, so non-ASCII metadata round-trips.
_ARGFILE_CHARSET = "-charset\nFileName=UTF8\n-charset\nUTF8\n"


# Captured at import: some tests replace this module's `subprocess` with a stub.
_DEVNULL = subprocess.DEVNULL
_CompletedProcess = subprocess.CompletedProcess


# cjxl and djxl fail on a path of 260 characters or more ("Could not write
# jxl file", "couldn't load") even with Windows long paths enabled; given the
# extended-length prefix \\?\ they read and write it, Japanese included
# (measured with libjxl 0.12 on a 320-character path, 2026-10-22). A temp
# written beside the output adds ~50 characters to the source path, and 1459
# files of the owner's library reach the limit that way. exiftool handles long
# paths itself, and ImageMagick only ever sees short temp paths (and would
# read the '?' of the prefix as a wildcard), so only the two codecs get it.
_LONG_PATH_THRESHOLD = _common._LONG_PATH_THRESHOLD


_long_codec_paths = _common._long_codec_paths


_ansi_encodable = _common._ansi_encodable


def _argfile_via_stdin(*a, **k): return _common._argfile_via_stdin(_G, *a, **k)


# The PNG ImageMagick hands to the next step is a temp file, read once and
# deleted: zlib on 45 MP of 16-bit pixels cost ~6.5 s per photo, single-
# threaded (measured 2026-11-05: 9.0 s -> 2.4 s, same pixels in the JXL).
_TEMP_PNG_ARGS = ["-define", "png:compression-level=0",
                  # #635: without it ImageMagick writes an sRGB chunk INSTEAD of
                  # the iCCP when the profile is a standard sRGB one, and the
                  # "kept its ICC profile" check below refused the derivative.
                  "-define", "png:preserve-iCCP=true"]


def _run_captured(*a, **k): return _common._run_captured(_G, *a, **k)


_stderr_tail = _common._stderr_tail


def _timeout_detail(exc) -> str:
    """'<tool> timed out after <N>s': the tool and the limit that actually
    fired. Every timeout used to read "codec timed out after CJXL_TIMEOUT" —
    the twelve 120 s exiftool timeouts of the 2026-10-06 run were reported as
    900 s codec timeouts."""
    cmd = exc.cmd if isinstance(exc.cmd, (list, tuple)) else [exc.cmd]
    tool = Path(str(cmd[0])).stem if cmd and cmd[0] else "subprocess"
    return f"{tool} timed out after {exc.timeout:g}s"


def _exiftool_source(*a, **k): return _common._exiftool_source(_G, *a, **k)


def _xmp_block_args(*a, **k): return _common._xmp_block_args(_G, *a, **k)


def _run_exiftool_argfile(*a, **k): return _common._run_exiftool_argfile(_G, *a, **k)


# ---------------------------------------------------------------------------
# Encoding-parameter record: read the "cjxl d=X e=Y" tag the encoder stamps,
# classify the requested recompression against it, and restamp after encoding.
# ---------------------------------------------------------------------------

# The History entry a deliberate "--encode-tag off" writes: a reader that meets
# it before any record stops there (the lineage was discarded on purpose, an
# older entry's record must not come back).
_NO_RECORD_SEGMENT = _common._NO_RECORD_SEGMENT


_history_record = _common._history_record


_strip_encode_params = _common._strip_encode_params


_reconcile_gen = _common._reconcile_gen


_append_encode_entry = _common._append_encode_entry


_merge_lineage_blocks = _common._merge_lineage_blocks


# Files per exiftool call when reading the encode records. 100 rather than
# 400: on a hard disk a batch takes ~20-30 s, so the planning progress line
# moves; each extra exiftool start costs ~0.14 s (~3.5 s over 3 327 files).
_ENCODE_RECORD_BATCH = 100


def _read_encode_params_batch(paths: list) -> dict:
    """{path str: {'desc': str, 'software': str, 'params': (d,e)|None,
    'gen': int, 'pixels': int, 'origin': str|None}} with one exiftool call per
    _ENCODE_RECORD_BATCH files — per-file spawns were minutes on a library.
    A slow read reports its progress (paced like the directory scan).

    `gen` is the reconciled generation count (max of the stored gen= token
    and the lossy chain length), read from the field that carries the record.
    Files exiftool cannot read come back with empty strings and params None,
    which classifies as 'unknown' — the ON_UNKNOWN policy decides.
    'pixels' is width*height (0 = unknown), read in the same exiftool call for the worker memory cap.
    """
    info = {str(p): {"desc": "", "software": "", "history": "", "comment": "",
                     "params": None, "gen": 0,
                     "pixels": 0, "width": 0, "height": 0, "origin": None,
                     "scan": None, "thumb": False}
            for p in paths}
    index = {os.path.normcase(str(p)): str(p) for p in paths}
    if not paths:
        return info
    batch_lines = ["-j", "-s", "-s", "-s", "-XMP-dc:Description", "-Software",
                   "-XMP-xmpMM:HistoryParameters", "-XMP-exif:UserComment",
                   "-XMP-dc:Relation", "-ExposureTime", "-FNumber", "-FocalLength",
                   "-ImageWidth", "-ImageHeight",
                   "-charset", "FileName=UTF8", "-charset", "UTF8"]
    BATCH = _ENCODE_RECORD_BATCH
    # Quiet while it is fast (a warm cache); then a line per batch, at the
    # directory scan's growing gap, so a 3 327-file plan is not a wall of lines.
    _t0 = time.monotonic()
    _next = _t0 + _SCAN_QUIET_SECONDS
    _gap = _SCAN_REPORT_EVERY
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
            r = _run_captured([_get_exiftool_cmd(), "-@", argfile],
                              EXIFTOOL_BATCH_TIMEOUT, text=True,
                              stall=EXIFTOOL_BATCH_STALL)
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
                # Since v2.24.0 the record lives in the History entry
                # (_history_record); older files keep it in the two fields.
                history = _history_record(entry.get("HistoryParameters"))
                comment = str(entry.get("UserComment") or "")
                merged_entries, merged_stored = _merge_lineage_blocks(
                    desc, software, history)
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
                try:
                    width = int(entry.get("ImageWidth") or 0)
                    height = int(entry.get("ImageHeight") or 0)
                except (TypeError, ValueError):
                    width = height = 0
                pixels = width * height
                # jxlphoto-origin (ORIGIN_PREFIX): what the first master came
                # from, for the --on-regeneration guard (_hidden_generations).
                rel = entry.get("Relation")
                rel_values = [] if rel is None else (
                    rel if isinstance(rel, list) else [rel])
                origin = None
                for t in rel_values:
                    t = str(t).strip()
                    if t.startswith(ORIGIN_PREFIX) and len(t) > len(ORIGIN_PREFIX):
                        origin = t[len(ORIGIN_PREFIX):]
                        break
                info[index[key]] = {"desc": desc, "software": software,
                                    "history": history, "comment": comment,
                                    "params": params, "gen": gen,
                                    "pixels": pixels, "width": width,
                                    "height": height, "origin": origin,
                                    "scan": _scan_kind_of(
                                        rel_values, software,
                                        camera=any(entry.get(k) is not None for k in
                                                   ("ExposureTime", "FNumber",
                                                    "FocalLength"))),
                                    "thumb": (any(str(t).strip() == THUMB_FLAG
                                                  for t in rel_values)
                                              or _is_camera_thumbnail_page(
                                                  rel_values, width, height))}
        except Exception as e:
            logger.warning(f"Encode-tag batch failed ({e}); {len(chunk)} file(s) "
                           f"treated as unknown origin")
        finally:
            if argfile:
                try:
                    os.unlink(argfile)
                except OSError:
                    pass
            _done = min(i + BATCH, len(paths))
            _now = time.monotonic()
            if _done < len(paths) and _now >= _next:
                # Remaining time at the rate so far (the jbrd walk and the
                # output checks after it are seconds: see "Planned in").
                _eta = (_now - _t0) / _done * (len(paths) - _done)
                logger.info(f"  Encode records: {_done}/{len(paths)} read "
                            f"({_fmt_secs(_now - _t0)}, ~{_fmt_eta(_eta)} left)")
                _gap = min(_gap * _SCAN_REPORT_FACTOR, _SCAN_REPORT_MAX)
                _next = _now + _gap
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


# Origins whose first master already came out of a lossy step the gen= chain
# never saw: a JPEG, a JXL from another program that jxlinfo read as lossy,
# or one whose kind was not recorded (plain `jxl`, v2.10.0 — unknown is
# treated as lossy, the side that only asks). Each counts as ONE hidden
# generation for the --on-regeneration guard; the stored gen= never changes.
_ORIGINS_WITH_HIDDEN_LOSS = ("jpeg", "jxl", "jxl-lossy")


def _hidden_generations(origin) -> int:
    """1 when `origin` (the jxlphoto-origin value, or None) names a lossy step
    before the first encode this toolkit recorded, else 0. tiff8/tiff16 and
    png8/png16/png are lossless sources; None or an unknown value adds
    nothing (the guard then behaves exactly as before v2.11)."""
    return 1 if origin in _ORIGINS_WITH_HIDDEN_LOSS else 0


def _regeneration_action(gen: int, new_d: float, origin=None):
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

    `origin` is the source's jxlphoto-origin value: a JPEG or lossy third-party
    origin is one generation the chain never recorded (_hidden_generations), so
    a gen=1 file made from one already counts as two.
    """
    if gen + _hidden_generations(origin) >= 2 and new_d > 0:
        return ON_REGENERATION
    return None


def _scan_kind_of(relation_values, software: str, camera: bool = True):
    """"raw"/"final" for a JXL of a film scan, "unknown" for one recognised
    only by its IR page, its Software tag, or as a TIFF master with neither a
    Software tag nor camera EXIF (archived before the encoder wrote
    jxlphoto-scan), None otherwise — pure. `camera`: the file carries an
    exposure, aperture or focal length. "jxlphoto-scan:no" is the encoder's
    verdict on a TIFF without either tag that it took for a camera TIFF (its
    160x120 thumbnail page): not a scan."""
    tokens = [str(t).strip() for t in relation_values or ()]
    for t in tokens:
        if t.startswith(SCAN_PREFIX) and len(t) > len(SCAN_PREFIX):
            _v = t[len(SCAN_PREFIX):]
            return None if _v == "no" else _v
    for t in tokens:
        if t.startswith(SUBFILETYPE_PREFIX):
            try:
                if int(t[len(SUBFILETYPE_PREFIX):]) & 4:
                    return "unknown"
            except ValueError:
                pass
    sw = str(software or "").casefold()
    if any(str(n).casefold() in sw for n in SCANNER_SOFTWARE if str(n).strip()):
        return "unknown"
    # The encode record can sit in Software (--encode-tag software): only
    # the text around it says what wrote the TIFF.
    _plain = re.sub(r"(?:^|\|)\s*(?:gen=\d+|cjxl\s+d=[0-9.]+\s+e=\d+)\s*(?=\||$)", "", sw)
    from_tiff = any(t.startswith(ORIGIN_PREFIX + "tiff") for t in tokens)
    if from_tiff and not _plain.strip(" |") and not camera:
        return "unknown"
    return None


def _scan_action(scan_kind, new_d: float):
    """ON_SCAN when this request would re-encode a film scan LOSSY; None when
    the guard does not apply (not a scan, a lossless request, a derivative,
    or a final scan under SCAN_SCOPE "raw"). An unmarked scan counts as raw:
    the side that keeps the file."""
    if scan_kind is None or new_d <= 0 or DERIVATIVE:
        return None
    if str(SCAN_SCOPE).lower() == "raw" and scan_kind == "final":
        return None
    return ON_SCAN


_gen_divergence_logged = False


def _log_gen_notes_once(*a, **k): return _common._log_gen_notes_once(_G, *a, **k)


def _encode_tag_mode(*a, **k): return _common._encode_tag_mode(_G, *a, **k)


def _visible_record_args(*a, **k): return _common._visible_record_args(_G, *a, **k)


def _next_record(desc: str, software: str, history: str = "") -> str:
    """The output's encode record: the source's chain (the union of its
    dc:Description, Software and History records) with this run's
    "cjxl d= e=" appended and gen= reconciled."""
    merged_entries, merged_stored = _merge_lineage_blocks(desc, software, history)
    merged_chain = " | ".join(
        ([f"gen={merged_stored}"] if merged_stored else [])
        + [f"cjxl d={d} e={e}" for d, e in merged_entries])
    record, stored, counted, _o = _append_encode_entry(
        merged_chain, CJXL_DISTANCE, CJXL_EFFORT)
    _log_gen_notes_once(stored, counted)
    return record


def _restamp_args(desc: str, software: str, label: str = "", history: str = "",
                  comment: str = "", record: str = "") -> list:
    """exiftool argfile lines for the record's VISIBLE copies (v2.24.0): the
    new record (`record`, or computed by _next_record) where ENCODE_TAG_MODE
    shows it, and the machine block stripped from every other field holding
    one (user text kept), so no stale d=/e= survives to mislead a later run.
    The record itself goes to the History entry (_history_args(record=...)).

    The chain is append-only and the union of every field that carried it
    (dc:Description, Software, History): replacing it, or dropping the old
    field's chain when the field changes, would silently undercount
    generations in the damaging direction.
    `label` (a file name) only prefixes the orphan-strip log lines.
    """
    if not record:
        record = _next_record(desc, software, history)
    return _visible_record_args(_encode_tag_mode(), record, desc, software, comment,
                                label or "a file")


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


def _decode_jxl_for_verify(jxl_path: Path, tmp_dir: Path,
                           require_original_space=False):
    """Decode a JXL back to pixels. Raises on any failure — a verification that
    cannot run must never read as a verification that passed.

    require_original_space: also raise when djxl did not return the pixels in
    the file's own colour space (a lossy ICC blob decodes to linear sRGB), so
    a comparison against the SOURCE pixels says why it cannot run (#584)."""
    png_path = tmp_dir / "verify.png"
    icc_args, out_icc, orig_icc = (_djxl_icc_args(tmp_dir) if require_original_space
                                   else ([], None, None))
    r = _run_captured(["djxl", str(jxl_path), str(png_path)] + icc_args, CJXL_TIMEOUT)
    if r.returncode != 0 or not png_path.exists():
        raise RuntimeError(f"djxl: {_stderr_tail(r.stderr)}")
    if require_original_space and _decoded_in_original_space(out_icc, orig_icc) is False:
        raise RuntimeError(
            "lossy ICC blob: djxl decodes it to linear sRGB, so the pixel check "
            "cannot compare it with the source — source kept "
            "(ICC_PNG_STRATEGY 'cautious' never writes such files)")
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


def _plan_item_would_skip(it) -> bool:
    """Would the real run report SKIP for this planned item?

    Mirrors convert_one's SKIP decision (which never applies to an in-place
    item): an existing, up-to-date output means SKIP, not a conversion. A
    derivative item whose existing output names ANOTHER recipe
    (_FORCE_REDERIVE) is re-derived, not skipped. Used by the dry-run preview
    and by the space preflight, which must not count skips."""
    if it["action"] == "skip_uptodate":
        return True
    return (not it["in_place"] and it["action"] in ("convert", "copy")
            and os.path.normcase(str(it["final"])) not in _FORCE_REDERIVE
            and _would_skip(it["src"], it["final"]))


def _derived_keep_note(label):
    """Why an existing derivative that is NOT re-derived is worth a summary
    line: "better" (kept because its encode outranks this run's — only the
    lower-effort setting would re-derive it), "unrecorded" (its label predates
    the distance/effort record), or None."""
    if not REDERIVE_ON_ENCODE_CHANGE or OVERWRITE is True:
        # --overwrite rewrites them anyway: the "run once with --overwrite"
        # hint would be false there.
        return None
    if not REDERIVE_ON_LOWER_EFFORT and _rederive_reason(label, _DERIVED_LABEL, True, True):
        return "better"
    if _split_encode_suffix(label)[1] is None:
        return "unrecorded"
    return None


def _mark_up_to_date_items(items):
    """Mark the planned items whose output is already up to date as
    "skip_uptodate", BEFORE classification reads anything from them.

    convert_one SKIPs an existing, up-to-date output before it looks at the
    item's action, so neither the encode record nor the jbrd box can change
    the outcome of such an item — reading them was most of a sync's planning
    time. Two stats per item decide it (_would_skip).

    Never on a run that deletes (the delete gate and its multi-page veto must
    see a fully planned item), never with --overwrite (nothing is a skip) and
    never in place (an in-place item is never a skip). A derivative run first
    reads the existing outputs' jxlphoto-derived labels: only an output that is
    this run's own derivative with no reason to re-derive is marked; anything
    else (another recipe, not a derivative, unreadable) goes through the full
    plan and the derivative check in main(), exactly as before.

    Returns (marked, derived "unrecorded" notes, derived "better" notes) — the
    notes are counted into main()'s derivative summary lines."""
    if DELETE_SOURCE or DELETE_SKIPPED or OVERWRITE is True:
        return 0, 0, 0
    cands = [it for it in items
             if not it["in_place"] and _would_skip(it["src"], it["final"])]
    n_unrecorded = n_better = 0
    if DERIVATIVE and cands:
        labels = _read_derived_markers_batch([it["final"] for it in cands])
        kept = []
        for it in cands:
            lab = labels.get(str(it["final"]), False)
            if lab is False or lab is None:
                continue
            if _rederive_reason(lab, _DERIVED_LABEL, REDERIVE_ON_ENCODE_CHANGE,
                                REDERIVE_ON_LOWER_EFFORT):
                continue
            note = _derived_keep_note(lab)
            n_better += note == "better"
            n_unrecorded += note == "unrecorded"
            kept.append(it)
        cands = kept
    for it in cands:
        it["action"] = "skip_uptodate"
        it["category"] = "up to date"
        it["reason"] = "output exists and is up to date"
    return len(cands), n_unrecorded, n_better


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
        export_idx = _marker_anchor_index(parts[:-1], input_root, marker_lower)
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
        export_idx = _marker_anchor_index(parts[:-1], input_root, marker_lower)
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


# X-B (2026-11-03 audit): below the export marker, modes 6/7 also skip the
# transcoder's JXL output folders: its JPEG archives sit next to the
# encoder's masters, share their names and resolve to the SAME
# 16B_JXL_small output. (This tool's own folders are _is_own_output_path's.)
# (script, setting, shipped default); the shipped default is skipped too.
_OTHER_TOOL_FOLDER_SOURCES = (
    ("jxl_jpeg_transcoder.py", "EXPORT_JXL_FOLDER", "JXL_jpeg"),
    ("jxl_jpeg_transcoder.py", "JXL_SIBLING_FOLDER", "JXL_jpeg"),
    ("jxl_jpeg_transcoder.py", "CONVERTED_JXL_FOLDER", "converted_jxl"),
)
_OTHER_TOOL_FIXED_FOLDERS = frozenset()
_OTHER_TOOL_SUFFIX_SOURCES = ()
# Every folder the ENCODER writes masters into (modes 6/7, 1, 3/5): never
# skipped, even where another tool uses the same name — the transcoder's
# converted_jxl is also the encoder's mode 1 folder (M1 of the 261103 review).
_MASTER_FOLDER_SOURCES = (
    ("jxl_tiff_encoder.py", "EXPORT_JXL_FOLDER", "16B_JXL"),
    ("jxl_tiff_encoder.py", "CONVERTED_JXL_FOLDER", "converted_jxl"),
    ("jxl_tiff_encoder.py", "JXL_FOLDER_NAME", "JXL_16bits"),
)
_other_tool_folders_cache = None
def _sibling_str_settings(*a, **k): return _common._sibling_str_settings(_G, *a, **k)


def _other_tool_output_folders(*a, **k): return _common._other_tool_output_folders(_G, *a, **k)


def _skip_other_tool_output(*a, **k): return _common._skip_other_tool_output(_G, *a, **k)


def _log_other_tool_skips(*a, **k): return _common._log_other_tool_skips(_G, *a, **k)


def find_jxls_mode6(input_path: Path):
    """Mode 6: only JXLs inside folders containing EXPORT_MARKER (any subfolder)."""
    all_jxls = find_jxls_recursive(input_path)
    filtered = []
    marker_lower = EXPORT_MARKER.lower()
    skipped_other = 0
    for t in all_jxls:
        # Match only directory parts; the filename itself is not an anchor
        parts_str = list(t.parts[:-1])
        export_idx = _marker_anchor_index(parts_str, input_path, marker_lower)
        if export_idx is not None:
            below = [p.lower() for p in parts_str[export_idx + 1:]]
            if _skip_other_tool_output(below, honor_requested_subfolder=False):
                skipped_other += 1
                continue
            filtered.append(t)
    _log_other_tool_skips(skipped_other)
    return sorted(filtered)


def find_jxls_mode7(input_path: Path):
    """Mode 7: only JXLs inside EXPORT_MARKER/EXPORT_JXL_SUBFOLDER."""
    all_jxls = find_jxls_recursive(input_path)
    filtered = []
    marker_lower = EXPORT_MARKER.lower()
    subfolder_lower = EXPORT_JXL_SUBFOLDER.lower()
    skipped_other = 0
    for t in all_jxls:
        # Match only directory parts; the filename itself is not an anchor
        parts_str = list(t.parts[:-1])
        export_idx = _marker_anchor_index(parts_str, input_path, marker_lower)
        if export_idx is None:
            continue
        if _skip_other_tool_output([p.lower() for p in parts_str[export_idx + 1:]]):
            skipped_other += 1
            continue
        if EXPORT_JXL_SUBFOLDER:
            if export_idx + 1 < len(parts_str) and parts_str[export_idx + 1].lower() == subfolder_lower:
                filtered.append(t)
        else:
            filtered.append(t)
    _log_other_tool_skips(skipped_other)
    return sorted(filtered)


# ---------------------------------------------------------------------------
# Per-file conversion
# ---------------------------------------------------------------------------

_exif_box_rank = _common._exif_box_rank


reorder_jxl_boxes = _common.reorder_jxl_boxes


_capture_output_identity = _common._capture_output_identity


_delete_partial_if_written = _common._delete_partial_if_written


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
#   A4:    the master's XMP carries the ORIGINAL ICC in base64 (the
#          jxlphoto-iccprofile token; CreatorTool up to v2.18.x); the
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


_png_chunk_types = _common._png_chunk_types


_JXLINFO_NOTES = set()
# The last answers of _jxlinfo_text, keyed by (path, size, mtime): one file is
# asked several times in a row (colour, orientation, delete gate).
_JXLINFO_CACHE = {}
_JXLINFO_CACHE_LOCK = threading.Lock()
_JXLINFO_CACHE_MAX = 256


def _jxlinfo_text(*a, **k): return _common._jxlinfo_text(_G, *a, **k)


_codestream_orientation = _common._codestream_orientation


_codestream_head = _common._codestream_head


_header_orientation = _common._header_orientation


def _jxl_orientation(*a, **k): return _common._jxl_orientation(_G, *a, **k)


def _orientation_reset_args(*a, **k): return _common._orientation_reset_args(_G, *a, **k)


_djxl_icc_args = _common._djxl_icc_args


_decoded_in_original_space = _common._decoded_in_original_space


_icc_a2b_only = _common._icc_a2b_only


# ─── Explorer thumbnail (EXPLORER_THUMBNAIL) ─────────────────────────────
# A small sRGB JPEG of the image in a box of the JXL container, for jxl-thumb
# (0.2.0+), which shows it in Windows Explorer instead of decoding the photo.
# The box is an ISO BMFF `uuid` box — the format's own way to carry a
# program's private data: decoders and editors that do not know it skip it
# (djxl, exiftool and this toolkit's box reorder keep it in place; exiftool's
# -tagsfromfile does not copy it, so a re-encode never inherits the source's).
# Not EXIF: a 768 px JPEG does not fit a JPEG's 64 KB EXIF segment, and
# djxl, which copies a JXL's EXIF into the JPEG it writes, then fails.
# Payload: the UUID, then b"jxlphoto-thumbnail:v1:<codestream bytes>:<FNV-1a
# 64 of its first 64 KiB>\n", then the JPEG. jxl-thumb shows it only while
# that fingerprint still names the file's codestream: a program that
# re-encodes the pixels and keeps the box would leave a stale one, ignored.
# Parity-pinned with the recompressor.
THUMB_BOX_UUID = _common.THUMB_BOX_UUID
THUMB_BOX_MARKER = _common.THUMB_BOX_MARKER
_THUMB_HEAD = _common._THUMB_HEAD


_jxl_boxes = _common._jxl_boxes


_codestream_fingerprint = _common._codestream_fingerprint


_thumbnail_box_fingerprint = _common._thumbnail_box_fingerprint


def _thumbnail_box_misplaced(jxl_path):
    """True when the file's thumbnail box sits AFTER the first codestream box,
    where a reader that stops at the image data never finds it — what
    reorder_jxl_boxes did to it before E-G (2026-11-03 audit) on every
    --restamp-moved / adopt stamp; False otherwise; None when the file cannot
    be read. Only the recompressor's refresh needs it (not parity-pinned)."""
    try:
        with open(jxl_path, "rb") as f:
            end = f.seek(0, 2)
            seen_codestream = False
            for typ, pos, hdr, size in _jxl_boxes(f, end):
                if typ in (b"jxlc", b"jxlp"):
                    seen_codestream = True
                elif (typ == b"uuid" and seen_codestream
                      and size - hdr >= 16 + len(THUMB_BOX_MARKER)):
                    f.seek(pos + hdr)
                    head = f.read(16 + len(THUMB_BOX_MARKER))
                    if (head[:16] == THUMB_BOX_UUID
                            and head[16:] == THUMB_BOX_MARKER):
                        return True
    except (OSError, struct.error):
        return None
    return False


def _move_thumbnail_before_codestream(jxl_path) -> None:
    """Put the file's existing thumbnail box back right before the codestream,
    its bytes unchanged (same JPEG, same fingerprint), through
    _write_thumbnail_box. Raises ValueError when there is none."""
    jpeg = fingerprint = None
    with open(jxl_path, "rb") as f:
        end = f.seek(0, 2)
        for typ, pos, hdr, size in _jxl_boxes(f, end):
            if typ == b"uuid" and size - hdr >= 16 + len(THUMB_BOX_MARKER):
                f.seek(pos + hdr)
                payload = f.read(size - hdr)
                if (payload[:16] == THUMB_BOX_UUID
                        and payload[16:16 + len(THUMB_BOX_MARKER)] == THUMB_BOX_MARKER):
                    rest = payload[16 + len(THUMB_BOX_MARKER):]
                    if b"\n" not in rest:
                        raise ValueError("malformed thumbnail box")
                    fp_bytes, jpeg = rest.split(b"\n", 1)
                    fingerprint = fp_bytes.decode("ascii")
                    break
    if jpeg is None:
        raise ValueError("no thumbnail box to move")
    _write_thumbnail_box(jxl_path, jpeg, fingerprint)


_write_thumbnail_box = _common._write_thumbnail_box


def _metadata_box_kinds(f, boxes):
    """'exif' / 'xml' / None for each box of `boxes` (from _jxl_boxes): a
    brob box counts as the kind it compresses."""
    kinds = []
    for typ, pos, hdr, size in boxes:
        if typ == b"brob" and size - hdr >= 4:
            f.seek(pos + hdr)
            typ = f.read(4)
        kinds.append({b"Exif": "exif", b"xml ": "xml"}.get(typ))
    return kinds


def _exif_after_xml(jxl_path):
    """True when an Exif box follows an XMP box: IrfanView then shows no
    EXIF. The encoder wrote that order from v2.17.0 to v2.19.0 (#561).
    False when it does not, None when the file cannot be read."""
    try:
        with open(jxl_path, "rb") as f:
            end = f.seek(0, 2)
            kinds = _metadata_box_kinds(f, _jxl_boxes(f, end))
    except (OSError, struct.error):
        return None
    return "xml" in kinds and "exif" in kinds[kinds.index("xml"):]


def _move_exif_first(jxl_path) -> None:
    """Rewrite `jxl_path` with its Exif box(es) moved to where its first XMP
    box was. Every other box keeps its bytes and order (the codestream and
    the thumbnail box included). Through a temp file beside it and
    os.replace."""
    jxl_path = Path(jxl_path)
    tmp = jxl_path.parent / f"{uuid.uuid4().hex}_{jxl_path.name}.tmp"

    def copy_box(f, out, pos, size):
        f.seek(pos)
        left = size
        while left:
            chunk = f.read(min(left, 1 << 22))
            if not chunk:
                raise OSError("short read")
            out.write(chunk)
            left -= len(chunk)

    try:
        with open(jxl_path, "rb") as f:
            end = f.seek(0, 2)
            boxes = _jxl_boxes(f, end)
            if not boxes or boxes[-1][1] + boxes[-1][3] != end:
                raise ValueError("unreadable container")
            kinds = _metadata_box_kinds(f, boxes)
            exif = [b for b, k in zip(boxes, kinds) if k == "exif"]
            with open(tmp, "wb") as out:
                placed = False
                for box, kind in zip(boxes, kinds):
                    if kind == "exif":
                        continue
                    if kind == "xml" and not placed:
                        for _t, pos, _h, size in exif:
                            copy_box(f, out, pos, size)
                        placed = True
                    copy_box(f, out, box[1], box[3])
                if not placed:
                    for _t, pos, _h, size in exif:
                        copy_box(f, out, pos, size)
        os.replace(str(tmp), str(jxl_path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


_JXL_THUMB_CLSID = "{EF830030-FBD6-4B62-BFD4-0DBD590D6135}"
_JXL_THUMB_HANDLER_KEYS = (
    r".jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}",
    r"SystemFileAssociations\.jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}",
)
_jxl_thumb_seen = []


def _jxl_thumb_installed(*a, **k): return _common._jxl_thumb_installed(_G, *a, **k)


def _explorer_thumbnail_wanted(*a, **k): return _common._explorer_thumbnail_wanted(_G, *a, **k)


def _make_explorer_thumbnail(*a, **k): return _common._make_explorer_thumbnail(_G, *a, **k)


def _add_explorer_thumbnail(*a, **k): return _common._add_explorer_thumbnail(_G, *a, **k)


def _jxlinfo_colour(jxl_path: Path):
    """('enum' | 'icc' | None, lossy True/False/None) from jxlinfo — a HEADER
    read, no decode. 'enum' = the file's colour space has a native JXL form,
    so cjxl straight from the JXL keeps it; 'icc' = the codestream carries an
    ICC blob, which needs the careful route (_recompress_encode). None when
    jxlinfo is missing or says something unexpected: the careful route then
    decides from a real decode."""
    out = _jxlinfo_text(jxl_path)        # one jxlinfo run per file, shared
    if out is None:
        return None, None
    kind = None
    if re.search(r"^\d+-byte ICC profile", out, re.M):
        kind = "icc"
    elif re.search(r"^Color space:", out, re.M):
        kind = "enum"
    m = re.search(r"^JPEG XL image, [^,]*, (lossy|\(possibly\) lossless)", out, re.M)
    lossy = None if not m else (m.group(1) == "lossy")
    return kind, lossy


# {(sha256 of the profile, grey): bool} — one tiny probe per profile per run.
_native_icc_cache = {}


def _tiny_png_with_icc(icc: bytes, grey: bool) -> bytes:
    """A 16x16 16-bit PNG carrying `icc` in an iCCP chunk (a gradient, so the
    encoder has something to encode)."""
    import zlib
    w = h = 16
    ch = 1 if grey else 3
    rows = []
    for y in range(h):
        row = bytearray(b"\x00")
        for x in range(w):
            for c in range(ch):
                row += struct.pack(">H", (x * 4000 + y * 300 + c * 9000) % 65536)
        rows.append(bytes(row))

    def chunk(t, data):
        return (struct.pack(">I", len(data)) + t + data
                + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 16, 0 if grey else 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"iCCP", b"icc\x00\x00" + zlib.compress(icc))
            + chunk(b"IDAT", zlib.compress(b"".join(rows)))
            + chunk(b"IEND", b""))


def _icc_has_native_form(icc: bytes, grey: bool) -> bool:
    """Does a LOSSY encode keep pixels carrying this profile in the profile's
    own space? Probed on a 16x16 image (cjxl -d 1, then djxl's
    --icc_out/--orig_icc_out) and cached per profile. False means cjxl would
    store it as an ICC blob, which djxl (and cjxl reading it back) returns as
    LINEAR sRGB. Any failure reads False: the caller then encodes the pixels
    tagged sRGB with the profile in XMP (the encoder's "skip"), which the
    toolkit always decodes correctly."""
    key = (hashlib.sha256(icc).hexdigest(), bool(grey))
    hit = _native_icc_cache.get(key)
    if hit is not None:
        return hit
    native = False
    try:
        with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
            tmp = Path(tmp)
            png, jxl, dec = tmp / "probe.png", tmp / "probe.jxl", tmp / "probe_dec.png"
            png.write_bytes(_tiny_png_with_icc(icc, grey))
            r = _run_captured([_get_cjxl_cmd() or "cjxl", str(png), str(jxl),
                               "-d", "1", "--effort", "1"], CJXL_TIMEOUT)
            if r.returncode == 0 and jxl.exists():
                icc_args, out_icc, orig_icc = _djxl_icc_args(tmp)
                r = _run_captured(["djxl", str(jxl), str(dec)] + icc_args, CJXL_TIMEOUT)
                if r.returncode == 0:
                    native = _decoded_in_original_space(out_icc, orig_icc) is True
    except Exception:
        native = False
    _native_icc_cache[key] = native
    return native


_png_iccp = _common._png_iccp


def _png_without_colour_chunks(src: Path, dst: Path) -> None:
    """Copy a PNG dropping iCCP/sRGB/gAMA/cHRM/cICP: cjxl then reads the
    pixels as sRGB and keeps their numbers — the encoder's "skip" layout,
    with the real profile carried in XMP (the jxlphoto-iccprofile token)."""
    drop = {b"iCCP", b"sRGB", b"gAMA", b"cHRM", b"cICP"}
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        sig = fi.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError(f"not a PNG: {src}")
        fo.write(sig)
        while True:
            head = fi.read(8)
            if len(head) < 8:
                break
            n = struct.unpack(">I", head[:4])[0]
            t = head[4:8]
            if t in drop:
                fi.seek(n + 4, 1)
                continue
            fo.write(head)
            remaining = n + 4
            while remaining:
                block = fi.read(min(remaining, 1 << 20))
                if not block:
                    raise RuntimeError(f"truncated PNG: {src}")
                fo.write(block)
                remaining -= len(block)
            if t == b"IEND":
                break


def _skip_icc_lines(jxl_path: Path, skip_icc: bytes) -> list:
    """[] when the source's XMP already records an ICC (the toolkit decoder
    assigns THAT one, so it keeps describing the pixels exactly as it did for
    the source; a v2.18.x CreatorTool copy moves to the token through
    _legacy_icc_lines), else the jxlphoto-iccprofile line that records
    `skip_icc` — the output's pixels are tagged sRGB and would otherwise lose
    their profile."""
    creator, tokens = _read_creator_and_relation(jxl_path)
    if _carried_icc(creator, tokens):
        return []
    return ["-XMP-dc:Relation+=" + _icc_profile_token(skip_icc)]


def _keep_invisible_flag() -> list:
    """--keep_invisible=1 for every LOSSY encode (E4 of the 2026-10-08 audit).
    A 2nd/4th channel reaches cjxl as alpha, and lossy cjxl otherwise rewrites
    the colour wherever it is 0 — for an RGB+IR scan kept as 4 channels, real
    image under the dust. A no-op for files without such a channel, and the
    channel count of a JXL is not known here without a decode."""
    return ["--keep_invisible=1"] if CJXL_DISTANCE > 0 else []


def _recompress_encode(jxl_path: Path, write_path: Path) -> tuple:
    """The plain (non-derivative) recompression, colour-safe.

    `cjxl src.jxl out.jxl` is right whenever the source's colour space has a
    native JXL form. It is NOT for a profile without one (table curves,
    scanner LUTs — the real film scans' SFprofT, A2B-only):
      * a LOSSLESS source re-encoded lossy became a "lossy ICC blob", which
        the toolkit decodes through linear sRGB — 27 dB on a real scan;
      * a source that already IS a lossy ICC blob is read by cjxl as LINEAR
        sRGB and written as native linear sRGB with the old ICC still in XMP
        — 18 dB on the real scan, 14 dB synthetic.
    So (mirroring the encoder): a lossy-blob source is never converted (the
    caller copies it verbatim); a source whose profile has no native form is
    decoded in its own space and encoded WITHOUT the profile (pixels tagged
    sRGB, profile in XMP — the encoder's "skip"); everything else goes
    straight through cjxl as before.

    Returns (route, skip_icc): route "encoded" (write_path written) or "blob"
    (nothing written); skip_icc = the profile the pixels are in when they were
    encoded tagged sRGB, else None.
    """
    def _cjxl(inp):
        cmd = ([_get_cjxl_cmd() or "cjxl", str(inp), str(write_path),
                "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT),
                # --container=1 UNCONDITIONALLY: a bare-codestream output is
                # refused by the exiftool restamp ("Will wrap JXL codestream").
                "--container=1",
                # strip=xmp: the restamp copies the source's XMP itself
                # (-tagsfromfile -XMP, as a block), so cjxl's carried copy is
                # redundant — and harmful: with it in place, exiftool ADDS
                # the copied list to the carried one wherever that same
                # call appends an entry (the xmpMM:History+= line), writing
                # every entry twice and doubling the list on every run.
                # EXIF is still carried (plain boxes via -api Compress=0).
                # The coder report for xmp-history has the probe.
                "-x", "strip=xmp"] + _keep_invisible_flag() + _cjxl_buffering_flag())
        r = _run_cjxl_full(cmd)
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {_stderr_tail(r.stderr)}")

    kind, _lossy = _jxlinfo_colour(jxl_path)
    if kind == "enum":
        _cjxl(jxl_path)
        return "encoded", None
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
        tmp = Path(tmp)
        dec = tmp / "dec.png"
        icc_args, out_icc, orig_icc = _djxl_icc_args(tmp)
        r = _run_captured(["djxl", str(jxl_path), str(dec)] + icc_args, CJXL_TIMEOUT)
        if r.returncode != 0 or not dec.exists():
            raise RuntimeError(f"djxl: {_stderr_tail(r.stderr)}")
        same = _decoded_in_original_space(out_icc, orig_icc)
        if same is False:
            return "blob", None
        if same is None:
            raise RuntimeError("djxl did not report the decoded and original colour "
                               "spaces (--icc_out/--orig_icc_out), so nothing proves "
                               "a re-encode keeps the colours — refusing")
        icc = orig_icc.read_bytes()
        grey = bytes(icc[16:20]) == b"GRAY"
        if CJXL_DISTANCE == 0 or _icc_has_native_form(icc, grey):
            _cjxl(jxl_path)
            return "encoded", None
        skip_png = tmp / "skip.png"
        _png_without_colour_chunks(dec, skip_png)
        _cjxl(skip_png)
        logger.info(f"  >Profile with no native JXL form: re-encoded tagged sRGB "
                    f"with the profile kept in XMP (the encoder's 'skip') | "
                    f"{jxl_path.name}")
        return "encoded", icc


_png_is_grayscale = _common._png_is_grayscale


_png_has_alpha = _common._png_has_alpha


SHARPEN_PRESETS = {
    "screen": {"sigma": 0.8, "gain": 0.6, "threshold": 0.0},    # ~ Capture One "screen"
    "print":  {"sigma": 3.24, "gain": 0.77, "threshold": 0.0},  # = Capture One "print"
}
# Calibrated 2026-09-25/26 against Capture One's DEFAULT output-sharpening
# presets: the same 4 Nikon Z7 photos (16-bit Elle V2 g2.2) exported with
# sharpening off / screen / print at 1000, 2000, 3000 px and full resolution
# (8256 px), and a grid search of this Lab-L unsharp on the "off" export.
# C1 keeps the RADIUS nearly constant in output pixels (print ~3 px at every
# size); only the screen AMOUNT falls with size (best 0.8 at 1000 px -> 0.4 at
# 8256 px). print (3.24, 0.77) is within 0.1 dB of the per-size best at every
# size; screen (0.8, 0.6) is the best fixed compromise: <= 1.6 dB from the
# per-size best (1000: -1.3, 2000: -0.7, 3000: -0.2, full: -1.6 dB).
# The ONLY place the sharpening numbers live, calibrated against Capture One's
# own output sharpening. Units are OUTPUT pixels, so one preset works at any
# output size. sigma = gaussian sigma (px), gain = unsharp amount (1.0 = 100%),
# threshold = fraction of full scale below which a difference is left alone
# (protects sky, skin and noise).


_png_size = _common._png_size


_resize_geometry = _common._resize_geometry


def _sharpen_args(*a, **k): return _common._sharpen_args(_G, *a, **k)


_resize_label = _common._resize_label


_derived_label = _common._derived_label


# The derived label's encode record: "sRGB@long2048+screen/d4e9", "/d2e7s"
# when cjxl streamed the encode. A suffix, appended in main() — never inside
# _derived_label, which is parity-pinned with the transcoder (its derivatives
# carry no distance/effort).
_ENCODE_SUFFIX_RE = re.compile(r"/d([0-9.]+)e(\d+)(s?)$")

# A streamed lossy encode above this effort is this effort's file (cjxl 0.12,
# measured: docs/README_jxl_recompressor.md, "Streaming vs whole-image").
_STREAMED_EFFORT_CAP = 7


def _encode_suffix(distance, effort, floor, buffering=None):
    """'/d<d>e<e>' for the label, plus 's' when cjxl streams the encode
    (`buffering` = the --buffering value cjxl receives, None when it chooses).
    A lossy distance below this cjxl's floor is recorded AS the floor: cjxl
    clamps it to the same output, so 0.01 and 0.05 must not count as a change."""
    d = float(distance)
    if 0 < d < floor:
        d = floor
    streamed = not _cjxl_whole_image(d, int(effort), buffering)
    return f"/d{d:g}e{int(effort)}" + ("s" if streamed else "")


def _split_encode_suffix(label):
    """(recipe, (distance, effort, streamed)) — or (label, None) for a label
    written before the suffix existed. No 's' reads as a whole-image encode."""
    m = _ENCODE_SUFFIX_RE.search(label or "")
    if not m:
        return label, None
    return label[:m.start()], (float(m.group(1)), int(m.group(2)),
                               m.group(3) == "s")


def _encode_rank(enc):
    """Orders two encodes at the SAME distance: higher = a smaller file for the
    same quality target. A streamed lossy encode ranks at most as effort 7 (it
    is that file); at one effort a whole-image encode beats a streamed one."""
    d, e, streamed = enc
    if streamed and d > 0:
        e = min(e, _STREAMED_EFFORT_CAP)
    return (e, not streamed)


def _format_encode(enc):
    return f"d={enc[0]:g} e={enc[1]}" + (" streamed" if enc[2] else "")


def _rederive_reason(stored, wanted, check_encode, lower_effort=False):
    """Why an existing derivative must be re-derived, or None to keep it.
    A recipe change always wins; distance/effort only count when both labels
    record them and check_encode (REDERIVE_ON_ENCODE_CHANGE) is on. At the same
    distance only a better-ranked encode re-derives, unless lower_effort
    (REDERIVE_ON_LOWER_EFFORT) also accepts a worse one."""
    s_recipe, s_enc = _split_encode_suffix(stored)
    w_recipe, w_enc = _split_encode_suffix(wanted)
    if s_recipe != w_recipe:
        return f"derivative recipe changed ({s_recipe} -> {w_recipe})"
    if not check_encode or s_enc is None or w_enc is None:
        return None
    change = (f"encode settings changed ({_format_encode(s_enc)} -> "
              f"{_format_encode(w_enc)})")
    if abs(s_enc[0] - w_enc[0]) > 1e-9:
        return change
    s_rank, w_rank = _encode_rank(s_enc), _encode_rank(w_enc)
    if w_rank > s_rank:
        return change
    # Same rank = the same file (e9s and e7s): never worth an encode. Same
    # effort, recorded whole-image, now streamed: a record without the 's' may
    # predate the mark and BE that streamed encode — keep it.
    if (lower_effort and w_rank < s_rank
            and not (s_enc[1] == w_enc[1] and not s_enc[2] and w_enc[2])):
        return change
    return None


_xmp_icc_from_creator_tool = _common._xmp_icc_from_creator_tool


_carried_icc = _common._carried_icc


_icc_profile_token = _common._icc_profile_token


def _legacy_icc_lines(creator: str, tokens, migrate: bool = True) -> list:
    """exiftool lines that take a v2.18.x-style ICC:<base64> out of the
    output's CreatorTool (the copied XMP brought it along), keeping the real
    program name — an empty value removes the tag. With `migrate`, the same
    profile moves to a jxlphoto-iccprofile token when the source carries
    none, so the output keeps it. [] when CreatorTool holds no valid ICC
    (text that merely looks like one is left alone)."""
    legacy = _xmp_icc_from_creator_tool(creator)
    if legacy is None:
        return []
    lines = ["-XMP-xmp:CreatorTool=" + _argfile_safe(_creator_tool_without_icc(creator))]
    if migrate and not any(str(t).startswith(ICC_PROFILE_PREFIX) for t in tokens or ()):
        lines.append("-XMP-dc:Relation+=" + _icc_profile_token(legacy))
    return lines


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


def _read_creator_and_relation(*a, **k): return _common._read_creator_and_relation(_G, *a, **k)


def _read_origin_inputs(*a, **k): return _common._read_origin_inputs(_G, *a, **k)


_origin_for_output = _common._origin_for_output


def _derive_pixels(jxl_path: Path, write_path: Path) -> tuple:
    """Decode at 16 bits, apply the derivative recipe, re-encode.

    The recipe is any of: colour conversion to --output-icc, a Lanczos resize,
    Lab-L sharpening. The SOURCE profile is always assigned explicitly (trap
    A3): the encoder's original ICC carried in XMP (_carried_icc) — exactly
    what jxl_tiff_decoder attaches to the same pixels — else the iCCP djxl
    wrote (extracted to a file for magick), else sRGB (djxl writes an sRGB
    chunk, no iCCP, for sRGB-encoded files). Without --output-icc there is no
    conversion: the source profile is re-assigned after the Lab sharpening pass
    (trap B2), which drops it. Grey images are never colour-converted (no gamut
    to map; an RGB profile on a single channel is invalid — same rule as the
    transcoder), but resize and sharpening still apply.

    A lossy file carrying a whole ICC blob (a table-curve profile) decodes to
    LINEAR sRGB / linear grey instead of its own space: the source is then a
    float PFM decode with djxl's profile assigned, converted to the target —
    and a grey one back to its own grey profile.

    A profile with no native JXL form (table curves, scanner LUTs) would be
    written as a lossy ICC blob, which the toolkit decodes through linear
    sRGB: the encoded image is then the pixels tagged sRGB with the profile in
    XMP (the encoder's "skip"), and skip_icc names that profile. A conversion
    INTO an A2B-only profile (input/scanner profile, no B2A) is refused: it
    cannot be done faithfully.

    Returns (converted, size, skip_icc): converted True only for a colour
    conversion (the jxlphoto-iccprofile token then carries the TARGET
    profile); size is the
    resized (w, h) or None; skip_icc the profile bytes when the pixels were
    encoded tagged sRGB, else None. _derivative_metadata_args takes both instead of guessing again
    from the jxlphoto-grayscale marker: a grey JXL that does not carry the
    marker (not written by this toolkit) was left unconverted here but got the
    RGB target profile stamped into its CreatorTool.
    """
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
        tmp = Path(tmp)
        dec_png, conv_png = tmp / "dec.png", tmp / "conv.png"
        icc_args, out_icc, orig_icc = _djxl_icc_args(tmp)
        r = _run_captured(["djxl", str(jxl_path), str(dec_png), "--bits_per_sample=16"]
                          + icc_args, CJXL_TIMEOUT)
        if r.returncode != 0 or not dec_png.exists():
            raise RuntimeError(f"djxl: {_stderr_tail(r.stderr)}")
        # A lossy JXL carrying a whole ICC blob (table-curve profile) decodes
        # to LINEAR sRGB, not to the file's own space: the PNG must NOT be
        # labelled with the XMP ICC. The source then becomes a float PFM
        # decode with djxl's own profile assigned explicitly.
        blob = _decoded_in_original_space(out_icc, orig_icc) is False
        grey = _png_is_grayscale(dec_png)
        converted = False
        size = None
        if grey and not blob and not (RESIZE_MODE or SHARPEN != "none"):
            logger.info(f"  >Grayscale image: encoded without colour conversion | {jxl_path.name}")
            enc_in = dec_png
        else:
            args = []
            out_profile = None
            magick_in = dec_png
            if blob:
                if _png_has_alpha(dec_png):
                    # The float PFM carries no alpha, and alpha is not
                    # colour-managed: merging it back through the recipe
                    # (resize/sharpen) is not implemented. Fail closed
                    # rather than silently dropping the channel.
                    raise RuntimeError(
                        "lossy ICC blob with an alpha channel: the correct "
                        "decode goes through a float PFM, which has no alpha "
                        "— refusing to derive (decode to TIFF instead)")
                dec_pfm = tmp / "dec.pfm"
                float_icc = tmp / "float_out.icc"
                r = _run_captured(["djxl", str(jxl_path), str(dec_pfm),
                                   f"--icc_out={float_icc}"], CJXL_TIMEOUT)
                if r.returncode != 0 or not dec_pfm.exists():
                    raise RuntimeError(
                        f"djxl float decode: {_stderr_tail(r.stderr)}")
                logger.info(f"  >ICC blob in a lossy file: deriving from the "
                            f"float decode, CONVERTED from djxl's linear output "
                            f"| {jxl_path.name}")
                magick_in = dec_pfm
                # Assign djxl's own profile — never the XMP ICC, which
                # describes the ORIGINAL space these pixels are not in.
                args += ["-profile", str(float_icc)]
            if grey and blob:
                # A grey master with a table-curve grey profile (Photoshop
                # "Dot Gain", scanner LUTs) decodes to LINEAR grey just like
                # the RGB case. Grey is never colour-converted, so go back to
                # the file's own grey profile (djxl's --orig_icc_out: always
                # single-channel, unlike an XMP ICC that could be RGB). The
                # copied XMP ICC keeps describing these pixels.
                if _icc_a2b_only(orig_icc.read_bytes()):
                    raise RuntimeError(_A2B_ONLY_TARGET_MSG)
                args += ["-intent", "Relative", "-black-point-compensation",
                         "-profile", str(orig_icc)]
                out_profile = orig_icc
            elif not grey:
                creator, _tokens = _read_creator_and_relation(jxl_path)
                if blob:
                    if OUTPUT_ICC:
                        args += ["-intent", "Relative", "-black-point-compensation",
                                 "-profile", str(_OUTPUT_ICC_PATH)]
                        converted = True
                        out_profile = _OUTPUT_ICC_PATH
                    else:
                        # "keep" derivative: re-assigning linear sRGB would
                        # leave the master's space, so CONVERT to the original
                        # ICC (XMP, else the file's own as djxl reported it).
                        # converted stays False: the copied XMP already
                        # carries that same ICC.
                        src_icc = _carried_icc(creator, _tokens)
                        if src_icc:
                            orig_path = tmp / "orig_target.icc"
                            orig_path.write_bytes(src_icc)
                        else:
                            orig_path = orig_icc
                        if _icc_a2b_only(orig_path.read_bytes()):
                            raise RuntimeError(_A2B_ONLY_TARGET_MSG)
                        args += ["-intent", "Relative", "-black-point-compensation",
                                 "-profile", str(orig_path)]
                        out_profile = orig_path
                else:
                    src_icc = _carried_icc(creator, _tokens)
                    chunks = _png_chunk_types(dec_png)
                    if src_icc:
                        src_path = tmp / "src.icc"
                        src_path.write_bytes(src_icc)
                        args += ["+profile", "*", "-profile", str(src_path)]
                    elif "iCCP" in chunks:
                        # djxl's own iCCP, read from the chunk and assigned
                        # explicitly (#635): reading a PNG, ImageMagick drops
                        # an iCCP it recognises as a standard sRGB profile.
                        _icc = _png_iccp(dec_png)
                        if not _icc:
                            raise RuntimeError(
                                "the decoded PNG's iCCP profile could not be read — "
                                "refusing to guess the source colour space")
                        src_path = tmp / "src_iccp.icc"
                        src_path.write_bytes(_icc)
                        args += ["+profile", "*", "-profile", str(src_path)]
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
            cmd = (["magick", str(magick_in)] + args
                   + ["-depth", "16"] + _TEMP_PNG_ARGS + ["png:" + _magick_out_path(conv_png)])
            r = _run_captured(cmd, CJXL_TIMEOUT)
            if r.returncode != 0 or not conv_png.exists():
                raise RuntimeError(f"magick: {_stderr_tail(r.stderr)}")
            if (not grey or blob) and "iCCP" not in _png_chunk_types(conv_png):
                # Traps A1/A2: without the profile cjxl would silently tag the
                # converted pixels as sRGB. Never let that reach the archive.
                raise RuntimeError("ImageMagick wrote the converted image without its ICC "
                                   "profile — refusing to encode it as the wrong colour space")
            enc_in = conv_png
        skip_icc = None
        if CJXL_DISTANCE > 0:
            _icc_in = _png_iccp(enc_in)
            if _icc_in and not _icc_has_native_form(
                    _icc_in, bytes(_icc_in[16:20]) == b"GRAY"):
                # R1 (2026-10-08 audit): with the profile in iCCP cjxl would
                # write a lossy ICC blob — read back through linear sRGB (27 dB
                # on a real scan with its A2B-only profile). Encode the pixels
                # tagged sRGB instead; the profile rides in XMP
                # (the jxlphoto-iccprofile token).
                skip_png = tmp / "skip.png"
                _png_without_colour_chunks(enc_in, skip_png)
                enc_in = skip_png
                skip_icc = _icc_in
                logger.info(f"  >Profile with no native JXL form: derivative encoded "
                            f"tagged sRGB with the profile kept in XMP | {jxl_path.name}")
        cjxl_cmd = ([_get_cjxl_cmd() or "cjxl", str(enc_in), str(write_path),
                     "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT),
                     "--container=1", "-x", "strip=exif", "-x", "strip=xmp"]
                    + _keep_invisible_flag() + _cjxl_buffering_flag())
        r = _run_cjxl_full(cjxl_cmd)
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {_stderr_tail(r.stderr)}")
    return converted, size, skip_icc


_A2B_ONLY_TARGET_MSG = (
    "this lossy file stores an ICC blob whose profile has A2B tables but no B2A "
    "(an input/scanner profile): its pixels decode as linear sRGB and nothing can "
    "convert them back INTO that profile faithfully (the way back falls to the "
    "matrix/TRC — 27 dB on a real scan). Derive from the lossless master, or "
    "with --output-icc to a working space such as sRGB")


_collapse_list_reset = _common._collapse_list_reset


def _derivative_metadata_args(jxl_path: Path, converted: bool = True,
                              size=None) -> list:
    """exiftool lines that turn the copied master metadata into a derivative's:
    - dc:Relation: drop jxlphoto-src/srcsum (a derivative must never prove the
      original is archived — trap A7), drop any old jxlphoto-derived and the
      page-level icc:inherited flag, add jxlphoto-derived:<recipe>;
    - the jxlphoto-iccprofile token: replaced by the TARGET profile when the
      pixels were colour-converted, so the decoder labels a decoded TIFF with
      the colour space the pixels are really in (trap A4). When they were not
      (`converted` False: a grey image or a resize/sharpening-only recipe) the
      copied one is kept — it still describes the pixels;
    - CreatorTool: a v2.18.x ICC:<b64> blob is taken out (_legacy_icc_lines),
      moved to the token when the pixels were not converted;
    - pixel dimensions: corrected to the resized output's own (the XMP pair
      only when the source already carried it).
    """
    creator, tokens = _read_creator_and_relation(jxl_path)
    drop = (SRC_PREFIX, SRCSUM_PREFIX, DERIVED_XMP_PREFIX)
    if converted:
        drop = drop + (ICC_PROFILE_PREFIX,)
    keep = [t for t in tokens
            if t and not t.startswith(drop) and t != ICC_INHERITED_XMP_FLAG]
    lines = ["-XMP-dc:Relation="]
    lines += ["-XMP-dc:Relation+=" + _argfile_safe(t) for t in keep]
    lines.append("-XMP-dc:Relation+=" + DERIVED_XMP_PREFIX + _DERIVED_LABEL)
    if converted:
        lines.append("-XMP-dc:Relation+=" + _icc_profile_token(_OUTPUT_ICC_BYTES))
    lines += _legacy_icc_lines(creator, keep, migrate=not converted)
    if size:
        nw, nh = size
        lines.append(f"-ExifImageWidth={nw}")
        lines.append(f"-ExifImageHeight={nh}")
        r = _run_exiftool_argfile(
            ["-j", "-s", "-s", "-XMP-exif:PixelXDimension",
             "-XMP-exif:PixelYDimension", str(jxl_path)], timeout=EXIFTOOL_TIMEOUT)
        if r is not None and r.stdout:
            entry = json.loads(r.stdout)[0]
            if entry.get("PixelXDimension") is not None:
                lines.append(f"-XMP-exif:PixelXDimension={nw}")
            if entry.get("PixelYDimension") is not None:
                lines.append(f"-XMP-exif:PixelYDimension={nh}")
    return lines


def _origin_marker_lines(jxl_path: Path, has_record: bool) -> list:
    """The dc:Relation line giving a re-encoded output its jxlphoto-origin when
    the source has none and is clean: 'jpeg' for a jbrd source (the JPEG it
    reconstructs is the first master), 'jxl-lossy'/'jxl-lossless' for a JXL no
    toolkit script wrote (as jxlinfo reads its header; 'jxl' when it cannot
    tell). An inherited origin is already carried by the XMP block copy
    (and kept by _derivative_metadata_args), so nothing is added then."""
    try:
        if has_jbrd_box(jxl_path):
            fresh = "jpeg"
        else:
            # jxlinfo reads the header: XYB means lossy for certain; no XYB
            # means the LAST encode was lossless (the history before it is
            # unknown). No jxlinfo, or an answer it could not parse: plain jxl.
            _kind, _lossy = _jxlinfo_colour(jxl_path)
            fresh = {True: "jxl-lossy", False: "jxl-lossless"}.get(_lossy, "jxl")
    except Exception:
        return []
    tokens, record = _read_origin_inputs(jxl_path)
    value, inherited = _origin_for_output(tokens, has_record or record, fresh)
    if value is None or inherited:
        return []
    return ["-XMP-dc:Relation+=" + ORIGIN_PREFIX + _argfile_safe(value)]


def _orientation_marker_lines(jxl_path: Path) -> list:
    """The dc:Relation line recording the EXIF Orientation (2-8) the source's
    CODESTREAM carries (ORIENTATION_PREFIX): the re-encode turns the pixels
    upright and its own codestream says 1. Nothing when the codestream says 1,
    when neither jxlinfo nor the header read could tell (_jxl_orientation),
    or when the source already carries the token
    (written once; the XMP copy carries it)."""
    o = _jxl_orientation(jxl_path)
    if o in (None, 1):
        return []
    try:
        _creator, tokens = _read_creator_and_relation(jxl_path)
    except Exception:
        tokens = []
    if any(t.startswith(ORIENTATION_PREFIX) for t in tokens):
        return []
    return ["-XMP-dc:Relation+=" + ORIENTATION_PREFIX + str(o)]


def convert_one(jxl_path: Path, write_path: Path, final_path: Path,
                action: str, in_place: bool, desc: str, software: str,
                src_distance, history: str = "", comment: str = ""):
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
        skip_icc = None
        if DERIVATIVE:
            # djxl -> magick (recipe) -> cjxl; returns (converted, size|None)
            converted, size, skip_icc = _derive_pixels(jxl_path, write_path)
        else:
            converted, size = True, None
            # --container=1 UNCONDITIONALLY (inside _recompress_encode): at d=0
            # the gate used to omit it, so a bare-codestream source (any
            # third-party JXL) produced a bare output that the exiftool restamp
            # below refuses to edit — a guaranteed ERROR per file.
            route, skip_icc = _recompress_encode(jxl_path, write_path)
            if route == "blob":
                # A lossy file carrying an ICC blob: any re-encode reads it as
                # LINEAR sRGB and the colours are lost (R1 of the 2026-10-08
                # audit). Never convert it — keep the bytes, exactly like the
                # downgrade/jbrd "copy" policy.
                logger.warning(f"  >Lossy file with an ICC blob: a re-encode would "
                               f"go through linear sRGB and lose the colours — "
                               f"copied verbatim instead | {jxl_path.name}")
                if in_place:
                    try:
                        write_path.unlink()
                    except OSError:
                        pass
                    logger.info(f"[{n}/{total}] SKIP (lossy ICC blob: kept as it is) "
                                f"| {jxl_path.name}")
                    return (str(jxl_path), "skipped", str(final_path))
                shutil.copy2(str(jxl_path), str(write_path))
                if not _verify_jxl_integrity(write_path):
                    raise RuntimeError("copied file failed the JXL integrity check")
                logger.info(f"[{n}/{total}] COPY (lossy ICC blob) | {jxl_path.name} "
                            f"-> {final_path.name}")
                return (str(jxl_path), "copied", str(final_path))

        # Metadata: everything the source JXL carries (EXIF, XMP, the base64 ICC
        # token, jxlphoto-* provenance and multi-page group markers) copied
        # verbatim — then the encoding tag restamped to the NEW d/e, and a
        # v2.18.x CreatorTool ICC moved to the token (_legacy_icc_lines).
        #
        # -api Compress=0: cjxl >= 0.12 carries the source's Exif/XMP across
        # itself, as Brotli-compressed "brob" boxes, and exiftool edits them in
        # that form. IrfanView cannot read brob (README: "Viewer quirks"), so
        # without this the recompressed archive lost its visible EXIF even
        # with the boxes reordered — the encoder's outputs use plain boxes.
        if DERIVATIVE:
            _extra = _derivative_metadata_args(jxl_path, converted, size)
        else:
            # A v2.18.x master keeps its ICC in CreatorTool: the copy moves
            # it to the jxlphoto-iccprofile token and gives CreatorTool back
            # its program name. Cosmetic: when the read fails, the output
            # keeps the old copy (still read everywhere) — never an error.
            try:
                _ct, _toks = _read_creator_and_relation(jxl_path)
                _extra = _legacy_icc_lines(_ct, _toks)
            except Exception as _e_ct:
                logger.debug(f"CreatorTool ICC not moved ({_e_ct}) | {jxl_path.name}")
                _extra = []
        if skip_icc is not None and not (DERIVATIVE and converted):
            # The pixels are tagged sRGB; their real profile must reach the
            # XMP the decoder reads (already there for every encoder output;
            # a colour-converted derivative gets the TARGET profile above).
            _extra = _extra + _skip_icc_lines(jxl_path, skip_icc)
        # jxlphoto-origin for a source that has none (ORIGIN_PREFIX).
        _extra = _extra + _origin_marker_lines(jxl_path, src_distance is not None)
        # jxlphoto-orientation: what the JPEG's orientation was, before this
        # re-encode turned the pixels upright (ORIENTATION_PREFIX).
        _extra = _extra + _orientation_marker_lines(jxl_path)
        # Which toolkit version wrote this output (write-only; see
        # _history_args). A derivative is "derived", a re-encode "converted".
        # final_path, not write_path: the in-place temp is '<uuid>_<stem>.tmp'
        # (no extension left to name the type); only the MIME type is read.
        # The record rides on that entry (v2.24.0); "off" says there is none.
        _record = _next_record(desc, software, history)
        _extra = _extra + _history_args(
            "derived" if DERIVATIVE else "converted", jxl_path, final_path,
            record=_NO_RECORD_SEGMENT if _encode_tag_mode() == "off" else _record)
        # The XMP goes across as one BLOCK first, in a call of its own: a
        # field copy (-xmp:all) carries only what exiftool can WRITE and
        # dropped a namespace it can merely read (SilverFast's IR/iSRD
        # settings). In the SAME call as the edits below, exiftool would
        # write the block and ignore them; and the edit call must not copy
        # -xmp:all on top, or every list it appends to is written twice (#531).
        # A '%' in the source's name is expanded by -tagsfromfile: the output
        # would replace the master with none of its metadata (#550).
        _meta_tmp = tempfile.TemporaryDirectory(prefix="jxlrec_meta_", dir=TEMP_DIR)
        _src = _exiftool_source(jxl_path, _meta_tmp.name)
        _xmp_args = _xmp_block_args(jxl_path, _meta_tmp.name)
        r_xmp = (_run_exiftool_argfile(
            ["-overwrite_original", "-api", "Compress=0"] + _xmp_args + [str(write_path)],
            timeout=EXIFTOOL_TIMEOUT) if _xmp_args else None)
        if r_xmp is not None and r_xmp.returncode != 0:
            _meta_tmp.cleanup()
            raise RuntimeError(f"exiftool XMP copy: {_stderr_tail(r_xmp.stderr)}")
        # The block is in the file now, so a derivative's dc:Relation reset
        # must be all-`=` lines to replace it (_collapse_list_reset).
        r2 = _run_exiftool_argfile(
            _collapse_list_reset(
                ["-overwrite_original", "-api", "Compress=0",
                 "-tagsfromfile", _src,
                 # ExifByteOrder: the output's EXIF keeps the source's II/MM
                 # (cjxl wrote none, and a new EXIF block is MM by default).
                 "-exif:all", "-ExifByteOrder<ExifByteOrder", "-iptc:all"]
                # #623: the re-encode already turned the pixels by the
                # codestream orientation; the copied tag would turn them again.
                + _orientation_reset_args(jxl_path)
                + _restamp_args(desc, software, label=jxl_path.name, history=history,
                                comment=comment, record=_record)
                + _extra
                + [str(write_path)]), timeout=EXIFTOOL_TIMEOUT)
        _meta_tmp.cleanup()
        if r2.returncode != 0:
            # A failed metadata copy is an ERROR, not a warning: the output
            # would silently miss the ICC/EXIF this whole tool exists to keep.
            raise RuntimeError(f"exiftool metadata copy: {_stderr_tail(r2.stderr)}")

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

        # Explorer thumbnail (EXPLORER_THUMBNAIL), made from this output —
        # only after the keep-smaller decision, so its ~90 KB never decides
        # it. The source's never reaches it: the metadata copy above does not
        # carry the box (a verbatim copy keeps the source's, still valid).
        if status == "ok" and _explorer_thumbnail_wanted(CJXL_DISTANCE == 0):
            with tempfile.TemporaryDirectory(prefix="jxlrec_thumb_", dir=TEMP_DIR) as _ttmp:
                if (_add_explorer_thumbnail(write_path, _ttmp) == "written"
                        and not _verify_jxl_integrity(write_path)):
                    raise RuntimeError("output failed the JXL integrity check after "
                                       "the Explorer thumbnail was added")

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

    except subprocess.TimeoutExpired as e:
        if output_dirty:
            _delete_partial_if_written(write_path, final_path, _pre_identity)
        _error_details[str(jxl_path)] = _timeout_detail(e)
        logger.error(f"[{n}/{total}] TIMEOUT | {jxl_path.name} | {_timeout_detail(e)}")
        return (str(jxl_path), "error", str(final_path))
    except Exception as e:
        if output_dirty:
            _delete_partial_if_written(write_path, final_path, _pre_identity)
        # A bare MemoryError has an empty message: record its type instead,
        # or the log line is blank and the end-of-run memory hint misses it.
        detail = str(e) or type(e).__name__
        _error_details[str(jxl_path)] = detail
        logger.error(f"[{n}/{total}] ERROR | {jxl_path.name} | {detail}")
        _abort_if_disk_full(write_path.parent if write_path is not None else jxl_path.parent,
                            _disk_full_need(jxl_path))
        return (str(jxl_path), "error", str(final_path))


@_holds_staging
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
        _record_source_identity(it["src"])      # X2: before anything reads it
        return ex.submit(convert_one, it["src"], it["write"], it["final"],
                         it["action"], it["in_place"], it["desc"],
                         it["software"], it["src_d"],
                         history=it.get("history", ""), comment=it.get("comment", ""))

    # In-place multi-page documents (jxlphoto-mpg) are replaced
    # all-or-nothing. Only their pages wait for the group: every other in-place
    # file is replaced the moment it settles, exactly as before — deferring
    # EVERY file to the end of the run kept every verified re-encode on disk at
    # once (a whole tree's worth of .tmp files, the disk-full abort far closer),
    # and a Ctrl+C or crash threw all of that work away and left the temps
    # behind (round 43). The markers are read once, batched, up front.
    _in_place_items = [it for it in items if it["in_place"]]
    _group_of = {}          # id(item) -> (folder, mpg id)
    _group_members = {}     # (folder, mpg id) -> [items]
    if len(_in_place_items) >= 2:
        _mpg_of, _complete = _read_mpg_markers([it["src"] for it in _in_place_items])
        for it in _in_place_items:
            g = _mpg_of.get(str(it["src"]))
            if g:
                # Same (folder, id) keying as the delete gate: two copies of
                # one split in different folders are different documents.
                key = (str(it["src"].parent), g)
                _group_of[id(it)] = key
                _group_members.setdefault(key, []).append(it)
    # A "group" with one page in this run has no sibling to hold together.
    for _key in [k for k, m in _group_members.items() if len(m) < 2]:
        for _it in _group_members.pop(_key):
            _group_of.pop(id(_it), None)
    _group_ready = {}       # key -> [items converted and verified, waiting]
    _group_settled = {}     # key -> how many members have settled (any status)

    def _replace_in_place(it, final_path):
        """Swap the verified re-encode over the source, atomically.

        Same volume: an atomic os.replace. Staging: a temp file in the
        DESTINATION folder first — in place, the destination IS the only copy,
        and a cross-volume shutil.move onto it is not atomic. A single file is
        replaced the moment it settles; a page of a multi-page group only once
        the whole group has (see _settle_in_place).

        Atomic is not the same as safe: a source rewritten by another process
        while it was being re-encoded (an export, a second run) would be
        replaced by the re-encode of its OLD version. The source must still be
        the file this run recorded before reading it; otherwise the re-encode
        is discarded and the new version stays.
        """
        src_str = str(it["src"])
        if _source_changed_since_read(it["src"]):
            _discard_in_place_temp(it)
            results[src_str] = ("error", str(final_path))
            _error_details[src_str] = _CHANGED_IN_PLACE
            logger.error(f"  REPLACE REFUSED, source kept | {final_path.name} | "
                         f"{_CHANGED_IN_PLACE}")
            return
        if staging_used:
            # Same .tmp rule as the in-place write temps: if the promotion
            # fails the temp stays behind (deliberately, see below) and must
            # not be adoptable as a NEW input.
            dest_tmp = (final_path.parent
                        / f"{uuid.uuid4().hex}_{final_path.stem}.tmp")
            # #572: the copy out of staging is checked BEFORE it replaces the
            # only copy — a cross-volume move that damages bytes without an
            # error (antivirus, a flaky USB bridge) used to be caught only
            # after the original was gone.
            try:
                _staged_size = it["write"].stat().st_size
                _staged_md5 = md5_of_file(it["write"])
            except OSError as e:
                logger.error(f"  REPLACE FAILED, original kept | "
                             f"{final_path.name} | staged re-encode unreadable: {e}")
                results[src_str] = ("error", str(final_path))
                return
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
                    _copy_ok = (dest_tmp.stat().st_size == _staged_size
                                and md5_of_file(dest_tmp) == _staged_md5
                                and _verify_jxl_integrity(dest_tmp))
                except OSError:
                    _copy_ok = False
                if not _copy_ok:
                    logger.error(f"  REPLACE REFUSED, original kept | {final_path.name} | "
                                 f"the copy out of staging is not the verified "
                                 f"re-encode (size/MD5/integrity)")
                    try:
                        dest_tmp.unlink()
                    except OSError:
                        pass
                    results[src_str] = ("error", str(final_path))
                    return
                try:
                    os.replace(str(dest_tmp), str(final_path))
                    moved = True
                except OSError as e:
                    # The complete new file survives as dest_tmp; the original
                    # is untouched. Leave the temp file in place (deleting it
                    # would destroy the only good copy) and say where it is.
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
                # Same cleanup as above: the original is intact, the temp is
                # worthless bytecode next to it (and .tmp-named now, but do not
                # leave it to rot).
                try:
                    it["write"].unlink()
                except OSError:
                    pass
                moved = False
        if not moved:
            results[src_str] = ("error", str(final_path))
            return
        if not _verify_jxl_integrity(final_path):
            logger.error(f"  Replaced file failed the final integrity check | {final_path.name}")
            results[src_str] = ("error", str(final_path))
            return
        promoted.add(src_str)
        logger.info(f" REPLACED (in place) | {final_path.name}")

    def _release_group(key):
        """Every page of the group has settled: replace them all, or — when any
        page did not convert (failed, refused, kept) — none of them. The same
        all-or-nothing rule _delete_gate applies to deletion. (A policy skip
        never reaches process_group; main's _hold_incomplete_in_place_groups
        demotes its runnable siblings first.)"""
        members = _group_members[key]
        ready = _group_ready.pop(key, [])
        if len(ready) < len(members):
            for it in ready:
                try:
                    it["write"].unlink()
                except OSError:
                    pass
                results[str(it["src"])] = ("skipped", str(it["final"]))
                logger.warning(
                    f" GROUP HELD IN PLACE | {it['src'].name} | a sibling page of "
                    f"the same multi-page document did not convert this run — no "
                    f"page of the group was replaced")
            return
        # Every page must still be the file this run read BEFORE the first
        # one is replaced: _replace_in_place would refuse only the changed
        # page, after its siblings were already swapped — one document, two
        # versions.
        changed = {id(it) for it in ready if _source_changed_since_read(it["src"])}
        if changed:
            for it in ready:
                _discard_in_place_temp(it)
                src_str = str(it["src"])
                if id(it) in changed:
                    results[src_str] = ("error", str(it["final"]))
                    _error_details[src_str] = _CHANGED_IN_PLACE
                    logger.error(f"  REPLACE REFUSED, source kept | "
                                 f"{it['src'].name} | {_CHANGED_IN_PLACE}")
                else:
                    results[src_str] = ("skipped", str(it["final"]))
                    logger.warning(
                        f" GROUP HELD IN PLACE | {it['src'].name} | a sibling page "
                        f"of the same multi-page document changed during the run "
                        f"— no page of the group was replaced")
            return
        for it in ready:
            try:
                _replace_in_place(it, Path(str(it["final"])))
            except Exception as e:
                # Runs in THIS thread, outside convert_one's own try: an
                # unexpected failure must settle the item, not kill the run.
                results[str(it["src"])] = ("error", str(it["final"]))
                _error_details[str(it["src"])] = f"promotion failed: {e}"
                logger.error(f"  PROMOTION FAILED | {it['src'].name} | {e}")

    def _settle_in_place(it, converted: bool) -> None:
        """Called once per settled in-place item (converted or not). A single
        file is replaced now; a group page waits until its last sibling."""
        key = _group_of.get(id(it))
        if key is None:
            if converted:
                _replace_in_place(it, Path(str(it["final"])))
            return
        if converted:
            _group_ready.setdefault(key, []).append(it)
        _group_settled[key] = _group_settled.get(key, 0) + 1
        if _group_settled[key] == len(_group_members[key]):
            _release_group(key)

    try:
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
                    _detail = str(e) or type(e).__name__
                    _error_details[src_str] = f"worker crashed: {_detail}"
                    logger.error(f"  WORKER CRASHED | {it['src'].name} | {_detail}")
                results[src_str] = (status, final_str)
                if status not in ("ok", "overwrite", "copied"):
                    if it["in_place"]:
                        # A page that did not convert still SETTLES: its group
                        # must learn it, or its siblings would wait forever.
                        _settle_in_place(it, converted=False)
                    continue
                try:
                    final_path = Path(final_str)
                    if it["write"] == final_path:
                        # No staging and not in place: written directly at the final path.
                        promoted.add(src_str)
                        _note_promoted(src_str, final_path)
                        continue
                    if not it["in_place"] and it["write"].parent == final_path.parent:
                        # Beside-the-final uuid temp (no staging): same volume, so
                        # os.replace is atomic and overwrites an existing output —
                        # _promote_from_staging's shutil.move would NOT (os.rename
                        # refuses an existing destination on Windows).
                        try:
                            _swap_into_place(it["write"], final_path)
                            moved = True
                        except RuntimeError as e:
                            # #615: the final path is not what this run planned.
                            logger.error(f"  REPLACE REFUSED | {final_path.name} | {e}")
                            _error_details[src_str] = str(e)
                            try:
                                it["write"].unlink()
                            except OSError:
                                pass
                            moved = False
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
                        _note_promoted(src_str, final_path)
                        continue
                    if it["in_place"]:
                        # Converted and verified by convert_one: replaced now,
                        # or — a page of a multi-page group — once the whole
                        # group has settled.
                        _settle_in_place(it, converted=True)
                        continue
                    _why = _output_changed_since_planned(final_path)
                    if _why:
                        results[src_str] = ("error", final_str)
                        _error_details[src_str] = (f"REFUSED to write {final_path.name}: "
                                                   f"{_why}")
                        logger.error(f"  REFUSED, kept in staging | {it['write'].name} -> "
                                     f"{final_path} | {_why}")
                        continue
                    moved = _promote_from_staging(it["write"], final_path)
                    if not moved:
                        results[src_str] = ("error", final_str)
                        continue
                    promoted.add(src_str)
                    _note_promoted(src_str, final_path)
                except Exception as e:
                    # The promotion block above runs in THIS thread, outside
                    # convert_one's own try: an unexpected failure here used to
                    # kill the whole run. Settle this item as an error instead.
                    results[src_str] = ("error", final_str)
                    _error_details[src_str] = f"promotion failed: {e}"
                    logger.error(f"  PROMOTION FAILED | {it['src'].name} | {e}")
    finally:
        # Interrupted (Ctrl+C) or crashed with group pages still waiting: their
        # originals are intact, so the verified temps are only orphans now —
        # .tmp-named, never adopted as input, but never swept either. Remove
        # them. A normal run leaves nothing here (every group was released).
        for _waiting in _group_ready.values():
            for it in _waiting:
                try:
                    it["write"].unlink()
                except OSError:
                    pass
        _group_ready.clear()

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
    _prog = _batch_read_state("Multipage markers", len(paths))
    for i in range(0, len(paths), BATCH):
        chunk = paths[i:i + BATCH]
        _batch_read_tick(_prog, i)
        argfile = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             dir=TEMP_DIR, encoding="utf-8",
                                             newline=chr(10)) as af:
                af.write(chr(10).join(batch_lines + [str(o) for o in chunk]))
                af.write(chr(10))
                argfile = af.name
            r = _run_captured([_get_exiftool_cmd(), "-@", argfile],
                              EXIFTOOL_BATCH_TIMEOUT, text=True,
                              stall=EXIFTOOL_BATCH_STALL)
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


def _read_mpg_pages_batch(paths: list):
    """({path str: declared page count | None}, complete) — the
    jxlphoto-pages:<N> the encoder writes on every page of a split.

    Read only for the sources the delete gate found in a group (#574), in the
    same batches as _read_mpg_markers. `complete` False when any batch could
    not be read: the gate then keeps those groups (fail closed)."""
    totals = {str(p): None for p in paths}
    index = {os.path.normcase(str(p)): str(p) for p in paths}
    if not paths:
        return totals, True
    batch_lines = ["-j", "-s", "-s", "-XMP-dc:Relation",
                   "-charset", "FileName=UTF8", "-charset", "UTF8"]
    BATCH = 400
    complete = True
    _prog = _batch_read_state("Multipage page counts", len(paths))
    for i in range(0, len(paths), BATCH):
        chunk = paths[i:i + BATCH]
        _batch_read_tick(_prog, i)
        argfile = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             dir=TEMP_DIR, encoding="utf-8",
                                             newline=chr(10)) as af:
                af.write(chr(10).join(batch_lines + [str(o) for o in chunk]))
                af.write(chr(10))
                argfile = af.name
            r = _run_captured([_get_exiftool_cmd(), "-@", argfile],
                              EXIFTOOL_BATCH_TIMEOUT, text=True,
                              stall=EXIFTOOL_BATCH_STALL)
            if r.returncode != 0 or not r.stdout:
                complete = False
                continue
            for entry in json.loads(r.stdout):
                src = entry.get("SourceFile")
                rel = entry.get("Relation")
                if src is None or rel is None:
                    continue
                key = os.path.normcase(str(Path(src)))
                if key not in index:
                    continue
                for token in (rel if isinstance(rel, list) else [str(rel)]):
                    token = str(token).strip()
                    if token.startswith(MULTIPAGE_PAGES_PREFIX):
                        try:
                            totals[index[key]] = int(token[len(MULTIPAGE_PAGES_PREFIX):])
                        except ValueError:
                            totals[index[key]] = -1     # unreadable: fail closed
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
    return totals, complete


def _reconstructed_jpeg_md5(jxl_path: Path) -> Optional[str]:
    """MD5 of the JPEG `djxl --reconstruct_jpeg` gives back from a lossless
    JPEG archive, or None when that cannot run or fails (djxl < 0.12, not an
    archive, a damaged one, an empty result)."""
    if not _tool_at_least("djxl", 0, 12):
        return None
    fd, tmp_name = tempfile.mkstemp(suffix=".jpg", prefix="jxlrec_", dir=TEMP_DIR)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        r = _run_captured(["djxl", "--reconstruct_jpeg", str(jxl_path), str(tmp)],
                          CJXL_TIMEOUT)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            return None
        return md5_of_file(tmp)
    except Exception:
        return None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _jbrd_overwrite_refusal(it) -> Optional[str]:
    """Why overwriting an existing lossless JPEG archive (jbrd) must be
    refused (#558), or None.

    A jbrd file never carries provenance markers, so the marker check let it be
    overwritten — yet this script copies such archives verbatim and
    --delete-source then deletes their source: the copy can be the only one.
    Allowed: the source's own bytes, or (both jbrd) the same JPEG back from
    djxl --reconstruct_jpeg (that photo archived again at another effort).
    Refused: a source that is no lossless archive at all, one that gives
    another JPEG back, and a file that cannot be compared (fail closed). A
    reconstruction that cannot run proves nothing: the overwrite goes ahead as
    before. Like every jbrd refusal, not relaxed by --no-overwrite-guard.
    """
    src, final = it["src"], it["final"]
    if not has_jbrd_box(final):
        return None
    try:
        if md5_of_file(src) == md5_of_file(final):
            return None
    except OSError as e:
        return (f"the existing output is a lossless JPEG archive (jbrd) that could "
                f"not be compared with this source ({e})")
    src_jbrd = it.get("jbrd")
    if src_jbrd is None:
        src_jbrd = has_jbrd_box(src)
    if not src_jbrd:
        return ("the existing output is a lossless JPEG archive (jbrd) and this source "
                "is not that archive — overwriting it would destroy the only bit-exact "
                "copy of that JPEG if its source was deleted; rename one of them or "
                "write into another folder")
    theirs, ours = _reconstructed_jpeg_md5(final), _reconstructed_jpeg_md5(src)
    if theirs is None or ours is None or theirs == ours:
        return None
    return ("the existing output is the lossless archive of a DIFFERENT JPEG (djxl "
            "--reconstruct_jpeg gives other bytes) — overwriting it would destroy that "
            "JPEG if its source was deleted; rename one of them or write into another "
            "folder")


def _foreign_overwrite_refusals(items):
    """[(item, reason)] for every item this run is about to OVERWRITE onto an
    existing output whose provenance markers name a DIFFERENT origin.

    This used to be a warning, and only in the folder-preserving modes: "the
    source is authoritative on the overwrite path". That holds for the NEW
    source only. The existing output may be the archive of a photo whose TIFF
    an earlier encoder run deleted — then a plain sync with no deletion (every
    scheduled preset is one) overwrote that photo's only file, with no
    warning at all in the collapsing modes where same-named sources meet
    (#268's missing half). So it is a refusal now, in every mode. A markerless
    output keeps the old behaviour: nothing to compare, and refusing would
    block every archive written before the markers existed — except a lossless
    JPEG archive, which never has markers and is judged by its bytes and its
    JPEG instead (#558, _jbrd_overwrite_refusal). An UNREADABLE one is refused
    (X-A of the 2026-11-03 audit: the batch failed or stalled, so "cannot
    tell" — it may be another photo's archive); --no-overwrite-guard turns
    that refusal into a warning, like the marker one.
    """
    targets = [it for it in items
               if not it["in_place"]
               and it["action"] in ("convert", "copy")
               and it["final"].exists()
               and not _would_skip(it["src"], it["final"])]
    if not targets:
        return []
    marks = _read_source_markers_batch(
        [it["final"] for it in targets] + [it["src"] for it in targets])
    refused = []
    for it in targets:
        out_info = marks.get(str(it["final"])) or {"src": None, "srcsum": None}
        src_info = marks.get(str(it["src"])) or {"src": None, "srcsum": None}
        if out_info.get("src") is None and out_info.get("srcsum") is None:
            # Markerless: nothing to compare — unless it is a lossless JPEG
            # archive, which never carries markers.
            why = _jbrd_overwrite_refusal(it)
            if why:
                refused.append((it, why))
                continue
            if out_info.get("unread") and not has_jbrd_box(it["final"]):
                # X-A: unreadable is "cannot tell", not "markerless".
                if not OVERWRITE_GUARD:
                    logger.warning(f"  OVERWRITE (overwrite guard off) | "
                                   f"{it['final'].name}: its provenance markers could "
                                   f"not be read")
                    continue
                refused.append((it, (
                    "the existing output's provenance markers could not be read (the "
                    "exiftool batch failed or stalled), so nothing proves it is not "
                    "another photo's archive — re-run once exiftool reads the folder "
                    "again")))
            continue
        if not _markers_match(out_info, src_info, PROVENANCE_CHECK):
            if not OVERWRITE_GUARD:
                logger.warning(f"  OVERWRITE (overwrite guard off) | {it['final'].name}: "
                               f"its provenance markers name a different origin")
                continue
            if src_info.get("unread"):
                refused.append((it, (
                    "this source's provenance markers could not be read (the exiftool "
                    "batch failed or stalled), so nothing proves the existing output "
                    "is its archive — re-run once exiftool reads the folder again")))
                continue
            refused.append((it, (
                f"the existing output's provenance markers name a different origin "
                f"({out_info.get('src') or out_info.get('srcsum')}) — it is another "
                f"photo's archive, and overwriting it would destroy that photo if "
                f"its TIFF was already deleted; rename one of them or write into "
                f"another folder"
                + ("" if PROVENANCE_CHECK == "content" else
                   " (if the sources MOVED, re-run with --provenance content)"))))
    return refused


def _hold_incomplete_in_place_groups(items):
    """All-or-nothing for multi-page documents REPLACED IN PLACE (mode 8, or
    mode 0 without an output folder).

    Each page of such a run is replaced by process_group, page by page; the
    per-file gates protect the bytes, but a page that is policy-skipped
    (downgrade/regeneration/jbrd) is left out of the batch while its siblings
    are still re-encoded — one document spread across two generations, exactly
    the damage the jxlphoto-mpg marker exists to prevent. The delete gate
    already keeps a group together when its sources are deleted; this extends
    the same rule to in-place replacement. Every runnable in-place page of an
    incomplete group is demoted to skip (nothing is replaced).

    Markers unreadable -> no group is known and nothing is vetoed (fail OPEN):
    unlike a deletion, a replacement is individually verified, so the worst
    case is a mixed group, never data loss.
    """
    in_place = [it for it in items if it["in_place"]]
    if len(in_place) < 2:
        return
    mpg_of, _complete = _read_mpg_markers([it["src"] for it in in_place])
    groups = {}
    for it in in_place:
        g = mpg_of.get(str(it["src"]))
        if g:
            # Same (folder, id) keying as the delete gate: two copies of one
            # split in different folders are different documents.
            groups.setdefault((str(it["src"].parent), g), []).append(it)
    runnable = ("convert", "copy")
    for members in groups.values():
        if all(m["action"] in runnable for m in members):
            continue
        for m in members:
            if m["action"] not in runnable:
                continue
            m["action"] = "skip"
            m["reason"] = ((m.get("reason") or "")
                           + " | in-place group held together: a sibling page "
                             "of the same multi-page document is not being "
                             "recompressed this run").strip(" |")
            logger.warning(
                f" GROUP HELD IN PLACE | {m['src'].name} | a sibling page of the "
                f"same multi-page document is not being recompressed — leaving "
                f"the whole group untouched")


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
        _replaced = (_changed_since_promoted(src_str, final_path)
                     if processed_this_run else None)
        if _source_changed_since_read(src):
            ok, reason = False, "source changed since this run read it (re-exported or rewritten during the run?) — its current version was never archived"
        elif not final_path.exists():
            ok, reason = False, "output missing"
        elif _replaced:
            ok, reason = False, _replaced                   # #618
        elif not _verify_jxl_integrity(final_path):
            ok, reason = False, "output failed integrity check"
        elif not _flush_to_disk(final_path):
            ok, reason = False, "output could not be flushed to disk"   # #617
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
            elif not _markers_prove_archive(out_info, src_info, PROVENANCE_CHECK):
                ok, reason = False, ("existing output was made from an EARLIER "
                                     "version of this master (its jxlphoto-srcsum "
                                     "differs) — re-run with --overwrite")
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
    # A group is only whole when every page the split produced is in this run
    # (#574): a single-file input, or a sibling filtered out by the mode or a
    # rename, made one page look like a lone group, and it was deleted while
    # its siblings stayed — the document split across two folders. Read only
    # for grouped sources that would otherwise be deleted.
    _short = {}             # group key -> (pages here, pages declared)
    _grouped = [m[0]["src"] for members in by_group.values()
                if all(x[3] for x in members) for m in members]
    if _grouped:
        _totals, _totals_complete = _read_mpg_pages_batch(_grouped)
        for _g, members in by_group.items():
            if not all(m[3] for m in members):
                continue
            declared = [_totals.get(str(m[0]["src"])) for m in members]
            if not _totals_complete or any(d == -1 for d in declared):
                _short[_g] = (len(members), None)
            elif any(d is not None and d > len(members) for d in declared):
                _short[_g] = (len(members), max(d for d in declared if d is not None))
    for _g, members in by_group.items():
        if all(m[3] for m in members) and _g not in _short:
            continue        # whole group passed (or a lone page): decide alone
        for m in members:
            if m[3]:
                vetoed.add(str(m[0]["src"]))
    _short_reason = {str(m[0]["src"]): _short[_g]
                     for _g, members in by_group.items() if _g in _short
                     for m in members}

    # Pass 3: apply.
    for it, status, final_path, ok, reason, settled in decisions:
        if not settled:
            continue
        if ok and str(it["src"]) in _short_reason:
            _k, _n = _short_reason[str(it["src"])]
            ok, reason = False, (
                f"only {_k} of the {_n} pages of its multi-page document are in "
                f"this run — the group is kept together" if _n else
                "the page count of its multi-page document could not be read — "
                "the group is kept together")
        elif ok and str(it["src"]) in vetoed:
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
                       f"--on-downgrade/--on-regeneration/--on-unknown/--on-scan to decide "
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


# ---------------------------------------------------------------------------
# --refresh-explorer-thumbnails: thumbnails for JXLs that already exist
# ---------------------------------------------------------------------------
def _codestream_sha256(jxl_path):
    """SHA-256 of the whole codestream (the bytes _codestream_fingerprint
    samples), or None. The refresh compares it before and after its edit."""
    h = hashlib.sha256()
    try:
        with open(jxl_path, "rb") as f:
            end = f.seek(0, 2)
            f.seek(0)
            if f.read(2) == b"\xff\x0a":
                spans = [(0, end)]
            else:
                spans, pos = [], 0
                while pos + 8 <= end:
                    f.seek(pos)
                    size, typ = struct.unpack(">I4s", f.read(8))
                    hdr = 8
                    if size == 1:
                        size = struct.unpack(">Q", f.read(8))[0]
                        hdr = 16
                    elif size == 0:
                        size = end - pos
                    if size < hdr:
                        return None
                    skip = {b"jxlc": 0, b"jxlp": 4}.get(typ)
                    if skip is not None and size - hdr >= skip:
                        spans.append((pos + hdr + skip, size - hdr - skip))
                    pos += size
            if not spans:
                return None
            for off, n in spans:
                f.seek(off)
                while n > 0:
                    chunk = f.read(min(n, 1 << 20))
                    if not chunk:
                        return None
                    h.update(chunk)
                    n -= len(chunk)
    except (OSError, struct.error):
        return None
    return h.hexdigest()


def _wrap_bare_codestream(path: Path) -> None:
    """Rewrite a bare codestream file (FF 0A) as a container holding it in a
    jxlc box: exiftool cannot add metadata to a bare one. The codestream
    bytes are unchanged."""
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\x0a":
            return
        f.seek(0)
        cs = f.read()
    head = (b"\x00\x00\x00\x0cJXL \r\n\x87\n"
            + struct.pack(">I4s4sI4s", 20, b"ftyp", b"jxl ", 0, b"jxl "))
    if 8 + len(cs) < 2 ** 32:
        jxlc = struct.pack(">I4s", 8 + len(cs), b"jxlc")
    else:
        jxlc = struct.pack(">I4sQ", 1, b"jxlc", 16 + len(cs))
    with open(path, "wb") as f:
        f.write(head + jxlc)
        f.write(cs)


def _refresh_one_thumbnail(jxl_path: Path, dry_run: bool):
    """(status, detail) for one existing JXL. status: written / removed /
    reordered / current / skipped / error. "reordered": only boxes were
    moved — the Exif box before the XMP (#561, files written by
    v2.17.0-v2.19.0 — IrfanView shows no EXIF otherwise) and/or the existing
    thumbnail box back before the codestream (E-G: a restamp or adopt stamp
    had moved it to the end; jxl-thumb still read it there, a reader that
    stops at the image data would not); written/removed
    do the Exif move too when needed and say so in the detail. The file is edited only through a temp
    copy beside it: the box rewrites, the integrity check and the WHOLE
    codestream compared byte for byte (SHA-256); then its own modified date
    goes back on and it replaces the file with an atomic same-folder
    os.replace. Keeping the date matters: the encoder's sync compares it with
    the TIFF's, and a TIFF re-exported since must still read as newer."""
    if has_jbrd_box(jxl_path):
        return "skipped", "JPEG recovery (jbrd) container: never edited"
    fp = _codestream_fingerprint(jxl_path)
    current = _thumbnail_box_fingerprint(jxl_path)
    misordered = _exif_after_xml(jxl_path)
    misplaced = _thumbnail_box_misplaced(jxl_path)
    if fp is None or current is None or misordered is None or misplaced is None:
        return "error", "not a JPEG XL file"
    action, why_not = None, ""      # action: "write" / "remove" / None
    if EXPLORER_THUMBNAIL == "off":
        if current:
            action = "remove"
        else:
            why_not = "no thumbnail"
    else:
        if EXPLORER_THUMBNAIL == "auto" and not _jxl_thumb_installed():
            _kind, lossy = _jxlinfo_colour(jxl_path)
            if lossy is None:
                why_not = "jxlinfo could not tell lossy from lossless"
            elif lossy:
                why_not = ("lossy (EXPLORER_THUMBNAIL = auto without jxl-thumb "
                           "installed: lossless only)")
        if not why_not and current != fp:
            action = "write"
    # A box rewrite (write/remove) places the thumbnail itself; only a file
    # left otherwise alone needs its misplaced box moved.
    relocate = bool(misplaced) and action is None
    if action is None and not misordered and not relocate:
        return ("skipped", why_not) if why_not else ("current", "")
    moved = "; ".join(m for m, on in (
        ("EXIF box moved before the XMP", misordered),
        ("thumbnail box moved before the codestream", relocate)) if on)
    status = {"write": "written", "remove": "removed", None: "reordered"}[action]
    if dry_run:
        return status, "dry run" + (f"; {moved}" if moved else "")
    st = jxl_path.stat()
    before = _codestream_sha256(jxl_path)
    if before is None:
        return "error", "could not read the codestream"
    tmp = jxl_path.parent / f"{uuid.uuid4().hex}_{jxl_path.name}.tmp"
    with tempfile.TemporaryDirectory(prefix="jxlrec_thumb_", dir=TEMP_DIR) as ttmp:
        try:
            shutil.copy2(str(jxl_path), str(tmp))
            _wrap_bare_codestream(tmp)
            if misordered:
                _move_exif_first(tmp)
            if relocate:
                _move_thumbnail_before_codestream(tmp)
            if action == "write":
                added = _add_explorer_thumbnail(tmp, ttmp)
                if added == "small" and not misordered:
                    return "skipped", "small file: decodes fast without one"
                if added == "small":
                    status = "reordered"
                elif added != "written":
                    return "error", "the thumbnail could not be made (see the warning)"
            elif action == "remove":
                _write_thumbnail_box(tmp, None, None)
            if _codestream_sha256(tmp) != before:
                return "error", "the codestream changed — file left untouched"
            if not _verify_jxl_integrity(tmp):
                return "error", "the edited copy failed the integrity check — file left untouched"
            os.utime(str(tmp), ns=(st.st_atime_ns, st.st_mtime_ns))
            if _changed_since(jxl_path, st):
                return "error", ("the file changed while its thumbnail was being made "
                                 "(another run?) — left untouched")
            os.replace(str(tmp), str(jxl_path))
        except Exception as e:
            return "error", str(e)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    return status, moved


def _refresh_explorer_thumbnails(files, workers: int, dry_run: bool) -> int:
    """Write, renew or remove Explorer thumbnails in existing JXLs, in place.
    Returns the number of errors."""
    _what = {"auto": ("every file: jxl-thumb is installed" if _jxl_thumb_installed()
                      else "lossless files: jxl-thumb is not installed"),
             "on": "every file", "off": "removing them"}
    logger.info(f"Explorer thumbnails ({EXPLORER_THUMBNAIL}: {_what[EXPLORER_THUMBNAIL]}) "
                f"for {len(files)} JXL(s){' — DRY RUN' if dry_run else ''}")
    counts = {"written": 0, "removed": 0, "reordered": 0, "current": 0, "skipped": 0,
              "error": 0}
    if not dry_run:
        _sweep_orphan_temps(files)
    t0 = time.time()
    lock = threading.Lock()

    def work(f):
        try:
            status, detail = _refresh_one_thumbnail(f, dry_run)
        except Exception as e:
            status, detail = "error", str(e)
        with lock:
            counts[status] += 1
            done = sum(counts.values())
            left = (time.time() - t0) / done * (len(files) - done)
            msg = (f"[{done}/{len(files)}] {status.upper()}"
                   f"{' (' + detail + ')' if detail else ''} | {f.name}"
                   f"{f'  ~{left / 60:.0f} min left' if done % 25 == 0 and left > 60 else ''}")
            (logger.error if status == "error" else logger.info)(msg)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
        list(pool.map(work, files))
    logger.info(f"Explorer thumbnails: {counts['written']} written, {counts['removed']} removed, "
                f"{counts['current']} already current, {counts['skipped']} skipped, "
                f"{counts['error']} error(s)")
    if counts["reordered"]:
        logger.info(f"EXIF moved before the XMP (IrfanView) in {counts['reordered']} more "
                    f"file(s) whose thumbnail needed nothing")
    return counts["error"]


# ---------------------------------------------------------------------------
def build_parser():
    """The command line (--flags only; main() validates and applies them).
    Its own function so the wrapper can check an expert flag against it
    (parse_known_args) before a run, and a test can compare it with the
    documentation."""
    parser = argparse.ArgumentParser(
        description="Batch JXL -> JXL recompressor (smaller archives, same metadata)")
    parser.add_argument("--version", action="version",
                        version=f"{TOOLKIT_NAME} {TOOLKIT_VERSION}")
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
                        help="Target JXL distance 0-25 (default: CJXL_DISTANCE setting, 1.0)")
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
    parser.add_argument("--on-scan", dest="on_scan", default=None,
                        choices=["ask", "copy", "skip", "convert"],
                        help="A film scan would be re-encoded lossy: ask/copy/skip/convert "
                             "(default: ON_SCAN setting, 'copy'). Derivatives and "
                             "lossless requests are never affected")
    parser.add_argument("--scan-scope", dest="scan_scope", default=None,
                        choices=["all", "raw"],
                        help="Which film scans --on-scan protects: all (default) or raw")
    parser.add_argument("--jbrd-policy", dest="jbrd_policy", default=None,
                        choices=["copy", "skip", "convert"],
                        help="JXLs with a jbrd box (JPEG bit-exact recoverable): "
                             "copy/skip/convert (default: JBRD_POLICY setting, 'copy')")
    parser.add_argument("--no-keep-smaller", dest="no_keep_smaller", action="store_true",
                        help="Keep the re-encoded file even when it is not smaller "
                             "than the source (default: fall back to a verbatim copy)")
    _rd = parser.add_mutually_exclusive_group()
    _rd.add_argument("--rederive-on-encode-change", dest="rederive_on_encode_change",
                     action="store_const", const=True, default=None,
                     help="Derivatives: re-derive an existing output whose recorded "
                          "distance/effort differ from this run's (default: "
                          "REDERIVE_ON_ENCODE_CHANGE setting, True)")
    _rd.add_argument("--no-rederive-on-encode-change", dest="rederive_on_encode_change",
                     action="store_const", const=False,
                     help="Derivatives: re-derive only when the colour/size/"
                          "sharpening recipe changes, never for distance/effort")
    _rl = parser.add_mutually_exclusive_group()
    _rl.add_argument("--rederive-on-lower-effort", dest="rederive_on_lower_effort",
                     action="store_const", const=True, default=None,
                     help="Derivatives: also re-derive when this run's effort is "
                          "LOWER than the recorded one at the same distance "
                          "(default: REDERIVE_ON_LOWER_EFFORT setting, False)")
    _rl.add_argument("--no-rederive-on-lower-effort", dest="rederive_on_lower_effort",
                     action="store_const", const=False,
                     help="Derivatives: keep an existing derivative encoded at a "
                          "higher effort than this run's at the same distance")
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
                        choices=["history", "comment", "title", "xmp", "software", "off"],
                        help="The new cjxl d=/e= always goes to xmpMM:History; a visible copy: "
                             "history (default: none), comment, title, software; xmp = "
                             "history; off = record nothing (default: ENCODE_TAG_MODE)")
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
                             "it. Every run refuses to overwrite an output whose markers "
                             "name a DIFFERENT origin; with --delete-source in a "
                             "collapsing mode every existing output must also match. "
                             "path (default) compares the recorded location; content "
                             "also accepts matching source bytes (moved folders)")
    parser.add_argument("--no-overwrite-guard", action="store_true",
                        help="Allow overwriting an existing output whose provenance "
                             "marker names a DIFFERENT source (a renamed or moved "
                             "folder, or another photo with the same name). Each such "
                             "overwrite is logged. For libraries whose sources are "
                             "never deleted; refused together with --delete-source. "
                             "Same as OVERWRITE_GUARD = False.")
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
    parser.add_argument("--explorer-thumbnail", choices=["auto", "on", "off"], default=None,
                        help="A small sRGB JPEG of the image stored in the output JXL, "
                             "shown by jxl-thumb in Windows Explorer: auto = lossless "
                             "outputs only (default), on = every output, off = none "
                             "(EXPLORER_THUMBNAIL)")
    parser.add_argument("--refresh-explorer-thumbnails", action="store_true",
                        help="Re-encode nothing: write or renew the Explorer thumbnail of "
                             "the JXLs this --mode would take as sources, in place (with "
                             "--explorer-thumbnail off: remove them). The modified date of "
                             "each file is kept, so sync runs are not fooled")
    parser.add_argument("--summary-json", action="store_true", help=argparse.SUPPRESS)
    return parser


_work_dir_problem = _common._work_dir_problem


def main():
    global OVERWRITE, DELETE_SOURCE, DELETE_CONFIRM, VERIFY_ROUNDTRIP, DELETE_SKIPPED
    global CJXL_DISTANCE, CJXL_EFFORT, CJXL_BUFFERING, TEMP2_DIR, ENCODE_TAG_MODE
    global ON_DOWNGRADE, ON_UNKNOWN, ON_SCAN, SCAN_SCOPE, JBRD_POLICY, KEEP_SMALLER, PROVENANCE_CHECK
    global OVERWRITE_GUARD
    global REDERIVE_ON_ENCODE_CHANGE, REDERIVE_ON_LOWER_EFFORT
    global ON_REGENERATION, EXPORT_MARKER, EXPORT_JXL_SUBFOLDER, EXPORT_JXL_FOLDER
    global OUTPUT_ICC, EXPLORER_THUMBNAIL
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

    # Every CLI-mutated, run-scoped global is reset UNCONDITIONALLY here, before
    # the flags are applied below. The assignments further down only fire when a
    # flag was passed, so a second main() in the SAME process (the test suite,
    # or anything importing this module) inherited the first run's armed state:
    # a `--delete-source --delete-confirm-off` run left DELETE_SOURCE on with no
    # confirmation for the next run, and `--staging` leaked TEMP2_DIR. The
    # encoder got exactly this reset in round 34 (#431); this is the same fix.
    # _RUN_DEFAULTS restores each global to its SCRIPT setting (captured at
    # import), so a user-edited setting survives while cross-run leakage does
    # not — in particular --staging must not erase a configured TEMP2_DIR.
    for _gname, _gvalue in _RUN_DEFAULTS.items():
        globals()[_gname] = _gvalue

    parser = build_parser()
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
        if not 0.0 <= args.distance <= 25.0:
            parser.error("--distance must be between 0 and 25")
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
    if args.on_scan is not None:
        ON_SCAN = args.on_scan
    if args.scan_scope is not None:
        SCAN_SCOPE = args.scan_scope
    if args.jbrd_policy is not None:
        JBRD_POLICY = args.jbrd_policy
    if args.no_keep_smaller:
        KEEP_SMALLER = False
    if args.rederive_on_encode_change is not None:
        REDERIVE_ON_ENCODE_CHANGE = args.rederive_on_encode_change
    if args.rederive_on_lower_effort is not None:
        REDERIVE_ON_LOWER_EFFORT = args.rederive_on_lower_effort
    if args.output_icc is not None:
        OUTPUT_ICC = args.output_icc.strip() or None
    if args.encode_tag is not None:
        ENCODE_TAG_MODE = args.encode_tag
    ENCODE_TAG_MODE = _encode_tag_mode()   # "xmp" = "history"
    if args.provenance is not None:
        PROVENANCE_CHECK = args.provenance
    if args.no_overwrite_guard:
        OVERWRITE_GUARD = False
    if args.export_marker is not None:
        # `is not None`, mirroring --export-subfolder: `if args.export_marker:`
        # treated --export-marker "" (an explicit "export NOTHING here") as
        # absent and silently kept the default marker instead.
        EXPORT_MARKER = args.export_marker
    if args.export_subfolder is not None:
        EXPORT_JXL_SUBFOLDER = args.export_subfolder
    if args.export_jxl_folder is not None:
        EXPORT_JXL_FOLDER = args.export_jxl_folder.strip()
    if args.explorer_thumbnail is not None:
        EXPLORER_THUMBNAIL = args.explorer_thumbnail
    if EXPLORER_THUMBNAIL not in ("auto", "on", "off"):
        parser.error(f"EXPLORER_THUMBNAIL = {EXPLORER_THUMBNAIL!r}: use 'auto', 'on' or 'off'")
    if args.refresh_explorer_thumbnails:
        _conflicts = [flag for flag, on in (
            ("output", args.output is not None), ("--delete-source", args.delete_source),
            ("--output-icc", args.output_icc is not None),
            ("--resize-*", any(getattr(args, a, None) is not None for a in
                               ("resize_long", "resize_short", "resize_percent"))),
            ("--sharpen", getattr(args, "sharpen", None) not in (None, "none"))) if on]
        if _conflicts:
            parser.error(f"--refresh-explorer-thumbnails edits existing files in place and "
                         f"encodes nothing: it cannot be combined with {', '.join(_conflicts)}")

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
    if not OVERWRITE_GUARD and DELETE_SOURCE:
        # setup_logger() runs below: print, like the neighbouring validations.
        print("ERROR: The overwrite guard is off (--no-overwrite-guard or "
              "OVERWRITE_GUARD = False) and this run deletes sources "
              "(--delete-source). That combination can overwrite the only "
              "copy of another photo and then delete the file that could "
              "rebuild it, so it is refused. Turn the guard back on for "
              "delete runs.")
        sys.exit(2)
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
    # (No "--provenance has no effect without --delete-source" warning any
    # more: since the 2026-10-08 audit every run that overwrites an existing
    # output checks its provenance first — _foreign_overwrite_refusals — and
    # this script's default is the smart sync, which does overwrite.)

    if OUTPUT_ICC:
        try:
            _OUTPUT_ICC_LABEL, _OUTPUT_ICC_BYTES = _resolve_output_icc(OUTPUT_ICC)
        except ValueError as e:
            parser.error(str(e))
        if _icc_a2b_only(_OUTPUT_ICC_BYTES):
            # An input (scanner/camera) profile with A2B tables and no B2A:
            # converting INTO it falls back to its matrix/TRC, which is not the
            # inverse of A2B0 — the derivative's colours would be wrong.
            parser.error(f"--output-icc {OUTPUT_ICC}: this profile has A2B tables "
                         f"but no B2A (an input/scanner profile), so nothing can be "
                         f"converted INTO it faithfully — pick an output/working "
                         f"space profile (sRGB, AdobeRGB, ProPhoto, ...)")

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
    # folders on disk is not a simulation. It never writes into the staging
    # folder, so an existing path is enough there; TEMP_DIR it does use (the
    # planning reads' argfiles), so that one must exist (_work_dir_problem).
    def _check_dir(path, label, dry_run_uses_it=False):
        _why = _work_dir_problem(path, label, args.dry_run, dry_run_uses_it)
        if _why:
            parser.error(_why)

    _check_dir(TEMP_DIR, "TEMP_DIR", dry_run_uses_it=True)
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
    _source_identity.clear()
    # Same placement as the encoder (#431): the progress counter is per-run
    # state and _reset_abort is kept as the delete-stats-only helper, so the
    # zeroing lives here. Without it a second in-process run showed [N+1/total].
    _counter["done"] = 0
    _floor = _min_effective_distance(_get_cjxl_cmd() or "cjxl")
    _warn_distance_clamp(CJXL_DISTANCE, _floor)

    if DERIVATIVE:
        # The buffering cjxl actually receives: below 0.12 the flag is dropped.
        _DERIVED_LABEL += _encode_suffix(
            CJXL_DISTANCE, CJXL_EFFORT, _floor,
            CJXL_BUFFERING if _cjxl_buffering_flag() else None)

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
    if not DERIVATIVE and args.rederive_on_encode_change is not None:
        logger.warning("--rederive-on-encode-change/--no-rederive-on-encode-change "
                       "only apply to derivatives (--output-icc/--resize-*/--sharpen) "
                       "— ignored")
    if not DERIVATIVE and args.rederive_on_lower_effort is not None:
        logger.warning("--rederive-on-lower-effort/--no-rederive-on-lower-effort "
                       "only apply to derivatives (--output-icc/--resize-*/--sharpen) "
                       "— ignored")
    elif (DERIVATIVE and args.rederive_on_lower_effort and not REDERIVE_ON_ENCODE_CHANGE):
        logger.warning("--rederive-on-lower-effort has no effect while re-deriving "
                       "on distance/effort change is off")

    logger.info(f"Input: {args.input}")
    logger.info(f"Mode: {args.mode} | distance: {CJXL_DISTANCE} | effort: {CJXL_EFFORT} | "
                f"workers: {args.workers} | output ICC: {_OUTPUT_ICC_LABEL or 'keep source'}")
    if not OVERWRITE_GUARD:
        logger.warning("Overwrite guard OFF: an existing output made from another "
                       "source path is overwritten (and logged) instead of refused.")
    if DERIVATIVE:
        logger.info(f"Derivative: {_DERIVED_LABEL} | resize: "
                    f"{_resize_label(RESIZE_MODE, RESIZE_VALUE, ALLOW_UPSCALE) or 'none'}"
                    f" | sharpen: {SHARPEN}"
                    f" | re-derive on distance/effort change: {REDERIVE_ON_ENCODE_CHANGE}"
                    f" (also on a lower effort: {REDERIVE_ON_LOWER_EFFORT})")
    logger.info(f"Policies: downgrade={ON_DOWNGRADE} | regeneration={ON_REGENERATION} | "
                f"scan={ON_SCAN}{' (raw only)' if str(SCAN_SCOPE).lower() == 'raw' else ''} | "
                f"unknown={ON_UNKNOWN} | "
                f"jbrd={JBRD_POLICY} | keep-smaller={KEEP_SMALLER}")
    # No --export-jxl-folder: the folder is the script setting, never compared
    # with a literal copy of its shipped value (AGENTS.md).
    if (OUTPUT_ICC and args.mode == 7 and args.export_jxl_folder is None
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
        files = _drop_derivative_inputs(find_jxls_mode6(args.input))
        input_root = args.input
    elif args.mode == 7:
        files = find_jxls_mode7(args.input)
        if not EXPORT_JXL_SUBFOLDER:
            files = _drop_derivative_inputs(files)
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

    if args.refresh_explorer_thumbnails:
        if (EXPLORER_THUMBNAIL != "off" and not args.dry_run
                and (shutil.which("djxl") is None or shutil.which("magick") is None)):
            logger.error("--refresh-explorer-thumbnails needs djxl and ImageMagick (magick) on PATH")
            sys.exit(1)
        n_err = _refresh_explorer_thumbnails(files, args.workers, args.dry_run)
        emit_summary_json(args.summary_json, ok=len(files) - n_err, overwritten=0,
                          skipped=0, errors=n_err, log_file=log_file, dry_run=args.dry_run)
        sys.exit(1 if n_err else 0)

    if (_explorer_thumbnail_wanted(CJXL_DISTANCE == 0) and not args.dry_run
            and (shutil.which("djxl") is None or shutil.which("magick") is None)):
        logger.warning("Explorer thumbnails need djxl and ImageMagick (magick) on PATH; "
                       "this run writes none (EXPLORER_THUMBNAIL / --explorer-thumbnail)")
        EXPLORER_THUMBNAIL = "off"
    if EXPLORER_THUMBNAIL == "auto" and CJXL_DISTANCE > 0 and _jxl_thumb_installed():
        logger.info("Explorer thumbnails: jxl-thumb is installed, so lossy outputs get "
                    "one too (EXPLORER_THUMBNAIL = auto; --explorer-thumbnail off: none)")

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
        # The same file or folder by another name too (#577): a junction,
        # subst drive, UNC share or 8.3 name of the input is still the input.
        # A path that does not exist yet is never "the same".
        if (os.path.normcase(os.path.abspath(str(a)))
                == os.path.normcase(os.path.abspath(str(b)))):
            return True
        try:
            return os.path.samefile(str(a), str(b))
        except OSError:
            return False

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

    # Up-to-date outputs first, from two stats each: convert_one SKIPs such an
    # item before it looks at its action, so the encode record and the jbrd
    # box below would be read only to be thrown away — on a sync where almost
    # everything is up to date that was nearly the whole planning time.
    _t_plan = time.monotonic()
    n_uptodate, n_derived_unrecorded, n_derived_better = _mark_up_to_date_items(items)
    if n_uptodate:
        logger.info(f"{n_uptodate} file(s) already up to date at the destination: "
                    f"skipped without reading them")
    _to_plan = [it for it in items if it.get("action") != "skip_uptodate"]

    # Classification reads the encoder's d=/e= record (batched) and the jbrd box.
    # Planning opens every file at least twice before the first [n/total] line:
    # on a hard disk that is ~0.15-0.25 s per file, and a 3327-file folder sat
    # almost 8 minutes with nothing on screen after "JXLs found". Announce the
    # phase, and time each part so the log shows where a slow plan went.
    _t_uptodate = time.monotonic() - _t_plan
    if _to_plan:
        logger.info(f"Planning {len(_to_plan)} file(s): reading each one's encode record "
                    f"and checking for a jbrd box — on a hard disk this can take "
                    f"several minutes...")
    _t_rec0 = time.monotonic()
    param_info = _read_encode_params_batch([it["src"] for it in _to_plan])
    _t_records = time.monotonic() - _t_rec0
    _t_jbrd = 0.0
    for it in _to_plan:
        info = param_info[str(it["src"])]
        it["desc"] = info["desc"]
        it["software"] = info["software"]
        it["history"] = info.get("history", "")
        it["comment"] = info.get("comment", "")
        it["src_d"] = info["params"][0] if info["params"] else None
        it["gen"] = info["gen"]
        it["pixels"] = info.get("pixels", 0)
        it["dims"] = (info.get("width", 0), info.get("height", 0))
        it["origin"] = info.get("origin")
        it["scan"] = info.get("scan")
        it["category"], it["reason"] = _classify(info["params"],
                                                 CJXL_DISTANCE, CJXL_EFFORT,
                                                 gen=it["gen"], floor=_floor)
        _t0 = time.monotonic()
        it["jbrd"] = has_jbrd_box(it["src"])
        _t_jbrd += time.monotonic() - _t0
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
        regen = _regeneration_action(it["gen"], CJXL_DISTANCE, it["origin"])
        if regen is not None:
            _hidden = _hidden_generations(it["origin"])
            _before = (f" (+{_hidden} before this toolkit: the first master "
                       f"came from '{it['origin']}')") if _hidden else ""
            it["reason"] += (f" | already at generation {it['gen']}{_before}: "
                             f"another lossy re-encode adds ~0.2-0.6 dB of loss "
                             f"on top of the byte savings (--on-regeneration)")
            combined = _more_conservative(it["action"], regen)
            if combined == "ask" and regen == "ask":
                it["category"] = "regeneration"   # prompt group of its own
            it["action"] = combined
        # Scan guard: a film scan is never re-encoded lossy unless asked
        # (--on-scan). Independent of the others: the more conservative wins.
        scan_act = _scan_action(it["scan"], CJXL_DISTANCE)
        if scan_act is not None:
            it["reason"] += (f" | film scan ({it['scan']}): a lossy re-encode "
                             f"loses what only a new scan could bring back "
                             f"(--on-scan)")
            combined = _more_conservative(it["action"], scan_act)
            if combined == "ask" and scan_act == "ask":
                it["category"] = "film scan"
            it["action"] = combined
        # A derivative of a split TIFF's THUMBNAIL page (the reduced preview it
        # carried, flagged or a camera's unflagged 160 px one:
        # _is_camera_thumbnail_page) is no delivery copy of the photo: none is
        # made. Not a policy skip — no flag would make one worth writing.
        it["thumb"] = info.get("thumb", False)
        if DERIVATIVE and it["thumb"]:
            it["action"] = "skip_thumbnail"
            it["reason"] = "thumbnail page of a split TIFF: no derivative is made of it"

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
    # An up-to-date item counts as one: before it skipped planning it was a
    # convert/copy like the others, and two sources sharing a destination are
    # a collision whether or not this run happens to write it.
    _writing = ("convert", "copy", "skip_uptodate")
    _planned_outputs.clear()        # run-scoped (#615)
    _promoted_identity.clear()      # run-scoped (#618)
    _abort_on_duplicate_outputs(
        [(it["src"], it["final"]) for it in items
         if it["action"] in _writing])
    if not args.dry_run:
        _sweep_orphan_temps(it["final"] for it in items
                            if it["action"] in _writing)
        _record_planned_outputs((it["src"], it["final"]) for it in items
                                if it["action"] in _writing
                                and not it.get("in_place"))

    # Timed from here, not from _t_plan: the batch prompt above waits on a
    # person, and that wait is not planning.
    _t_out0 = time.monotonic()

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
        # The up-to-date ones skipped above were judged on the same rules.
        _n_unrecorded = n_derived_unrecorded
        _n_better = n_derived_better
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
                else:
                    _why = _rederive_reason(lab, _DERIVED_LABEL,
                                            REDERIVE_ON_ENCODE_CHANGE,
                                            REDERIVE_ON_LOWER_EFFORT)
                    if _why:
                        _FORCE_REDERIVE.add(os.path.normcase(str(it["final"])))
                        logger.info(f" {_why}: re-deriving | {it['src'].name}")
                    else:
                        _note = _derived_keep_note(lab)
                        _n_better += _note == "better"
                        _n_unrecorded += _note == "unrecorded"
            if derived_refused:
                _ids = {id(it) for it in derived_refused}
                items = [it for it in items if id(it) not in _ids]
        if _n_unrecorded:
            logger.info(f"{_n_unrecorded} existing derivative(s) predate the "
                        f"distance/effort record and keep their old encode "
                        f"settings — run once with --overwrite to refresh them")
        if _n_better:
            logger.info(f"{_n_better} existing derivative(s) kept: already encoded "
                        f"at a higher effort than this run's at the same distance "
                        f"(--rederive-on-lower-effort re-derives them)")
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
                if out_info.get("unread") or src_info.get("unread"):
                    reason = ("the provenance markers could not be read (the exiftool "
                              "batch failed or stalled) — cannot prove the existing "
                              "output is this photo's archive; refusing to overwrite "
                              "it and delete the source (re-run once exiftool reads "
                              "the folder again)")
                else:
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
                    logger.error(f"  Details: {GUARD_DOC_URL}")
    else:
        # Every other run: an output about to be OVERWRITTEN whose markers
        # name a DIFFERENT origin is refused (see _foreign_overwrite_refusals).
        # Same bookkeeping as above, so a refused page still vetoes the
        # deletion of its multi-page siblings.
        _foreign = _foreign_overwrite_refusals(items)
        if _foreign:
            refused = []
            for it, reason in _foreign:
                refused.append(it)
                failures.append((str(it["src"]), reason))
                if args.dry_run:
                    logger.info(f" DRY | would REFUSE | {it['src'].name} | {reason}")
                else:
                    logger.error(f"REFUSED | {it['src'].name} | {reason}")
                    _log_rejected_file(str(it["src"]), f"provenance: {reason}")
            refused_ids = {id(it) for it in refused}
            if not args.dry_run:
                items = [it for it in items if id(it) not in refused_ids]
                provenance_refused = refused
                for _l in _guard_refusal_hint(False):
                    logger.error(_l)

    # In-place multi-page documents are replaced all-or-nothing: a page that
    # cannot take part must hold the whole group (process_group also vetoes a
    # page that fails at CONVERSION time). Runs in a dry run too, so the preview
    # matches the real run.
    _hold_incomplete_in_place_groups(items)

    _t_outputs = time.monotonic() - _t_out0
    logger.info(f"Planned in {_fmt_secs(_t_uptodate + _t_records + _t_jbrd + _t_outputs)} "
                f"(up-to-date check {_fmt_secs(_t_uptodate)}, "
                f"encode records {_fmt_secs(_t_records)}, jbrd check "
                f"{_fmt_secs(_t_jbrd)}, output checks {_fmt_secs(_t_outputs)})")

    # --- Dry run: report and stop ------------------------------------------
    if args.dry_run:
        # A refused item stays in `items` (its DRY | would REFUSE line was
        # already printed), but it must not inflate the plan or the would-delete
        # count: the real run removes it and deletes nothing for it.
        _plan = [it for it in items if id(it) not in refused_ids]

        # A skipped CONVERT admitted by --delete-skipped is deleted only when
        # its pre-existing output carries this source's markers (the real gate
        # now requires it in every mode); the preview must not promise a
        # deletion the run will KEEP. A skipped COPY is proven by the MD5.
        _dry_prov_ok = {}
        if DELETE_SOURCE and DELETE_SKIPPED:
            _sk = [it for it in _plan if _plan_item_would_skip(it)
                   and it["action"] == "convert" and it["final"].exists()]
            if _sk:
                _pm = _read_source_markers_batch(
                    [it["final"] for it in _sk] + [it["src"] for it in _sk])
                for it in _sk:
                    _dry_prov_ok[id(it)] = _markers_prove_archive(
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
                        and not _plan_item_would_skip(it))
        n_copy = sum(1 for it in _plan if it["action"] == "copy"
                     and not _plan_item_would_skip(it))
        n_skip = len(_plan) - n_convert - n_copy
        for it in _plan:
            if _plan_item_would_skip(it):
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
    # Only what this run will WRITE: a policy skip or an up-to-date output
    # needs no space (a nightly sync counted the whole library).
    if not args.no_preflight:
        _preflight_space([it for it in items
                          if it["action"] in ("convert", "copy")
                          and not _plan_item_would_skip(it)])

    # --- Work ----------------------------------------------------------------
    work_items = []
    n_policy_skip = 0
    n_thumb_skip = 0
    n_uptodate_skip = 0
    for it in items:
        if it["action"] == "skip_uptodate":
            # Not handed to convert_one: it was never classified, so if its
            # source changed since planning it must wait for the next run
            # rather than be converted past the downgrade/jbrd policies.
            n_uptodate_skip += 1
            logger.info(f" SKIP (exists) | {it['src'].name}")
            continue
        if it["action"] == "skip_thumbnail":
            n_thumb_skip += 1
            logger.debug(f" SKIP (thumbnail page) | {it['src'].name}")
            continue
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
            workers = args.workers
            _slots = None
            _to_encode = [it for it in work_items if it["action"] == "convert"]
            if _to_encode:
                workers, _slots = _memory_capped_pipeline(
                    args.workers, max(it.get("pixels", 0) for it in _to_encode),
                    CJXL_DISTANCE, CJXL_EFFORT, CJXL_BUFFERING,
                    cjxl_pixels=_largest_encode_pixels(_to_encode))
            # The semaphore only matters when some workers must wait for a slot.
            _set_cjxl_slots(_slots if _slots is not None and _slots < workers
                            else None)
            results, promoted = process_group(work_items, workers)
    except KeyboardInterrupt:
        interrupted = True
        logger.error("Interrupted (Ctrl+C) — finishing the summary with what is done.")
        # Fall through to the summary with partial results; exit 130 below.
    finally:
        _set_cjxl_slots(None)

    # EVERY planned item, not just the ones that ran: policy-skipped and
    # refused pages must still veto the deletion of their multi-page siblings.
    _delete_gate(items + provenance_refused, results, promoted)

    # --- Summary -------------------------------------------------------------
    ok = sum(1 for s, _f in results.values() if s == "ok")
    copied = sum(1 for s, _f in results.values() if s == "copied")
    overwritten = sum(1 for s, _f in results.values() if s == "overwrite")
    skipped = (sum(1 for s, _f in results.values() if s == "skipped") + n_policy_skip
               + n_thumb_skip + n_uptodate_skip)
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
    # In a derivative run a policy skip means NO derivative at all, not "up to
    # date"; folded into "skipped" above it read like a finished folder.
    if DERIVATIVE and n_policy_skip:
        logger.warning(f"{n_policy_skip} file(s) got NO derivative (SKIP (policy) above): "
                       f"the master is already at this distance or a finer one, at its "
                       f"regeneration limit, or carries no encode record. Pass "
                       f"--on-downgrade convert (or --on-regeneration / --on-unknown "
                       f"convert) to write them.")
    if n_thumb_skip:
        logger.info(f"{n_thumb_skip} thumbnail page(s) of split TIFFs skipped: a derivative "
                    f"is made of the photo, not of the preview its TIFF carried.")

    _n_mem = sum(1 for _src, _detail in failures
                 if any(s in str(_detail).lower() for s in _MEMORY_ERROR_SIGNATURES))
    if _n_mem:
        logger.warning(f"{_n_mem} error(s) look like the system ran out of "
                       f"memory — lower --workers or WORKER_MEMORY_FRACTION, "
                       f"or use --buffering 1. Sync mode retries them on the "
                       f"next run.")
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
