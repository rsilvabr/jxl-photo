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

import subprocess, os, platform, tempfile, threading, zlib, struct, logging, sys, shutil, uuid, hashlib, json
import math
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time  # noqa: F401 (tests reach the clock through the script: mod.time, AGENTS.md)
from typing import Any, Dict, Optional
import argparse
import numpy as np
import tifffile
from PIL import Image

# Who wrote a file: recorded in every output's xmpMM:History and printed by
# --version. NOT a user setting. Bumped in ALL FIVE scripts on every release,
# even when only one script changed — tests/test_toolkit_version.py pins the
# five to each other and to the newest section of docs/version_history.md.
TOOLKIT_NAME = "jxl-photo"
TOOLKIT_VERSION = "2.30.2-rc2"
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
try:
    _common = _import_common()
except ImportError as _e:
    if __name__ != "__main__":
        raise      # imported by the wrapper or a test: theirs to handle
    sys.exit(f"{Path(__file__).name}: {_e}")
_G = _common.Script(globals())

# Where a provenance refusal sends the user (docs/behavior_and_limitations.md).
GUARD_DOC_URL = (f"{TOOLKIT_URL}/blob/main/docs/behavior_and_limitations.md"
                 f"#renamed-folders-and-the-overwrite-guard")

_HISTORY_MIME = _common._HISTORY_MIME

# Module-level logger; setup_logger() replaces it with a configured instance when main() runs.
_LOGGER_NAME = "jxl_convert"       # setup_logger (jxl_photo_common) reads it
logger = logging.getLogger(_LOGGER_NAME)


def _verify_jxl_integrity(*a, **k): return _common._verify_jxl_integrity(_G, *a, **k)


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


_is_relative_to = _common._is_relative_to


_replace_suffix_token = _common._replace_suffix_token


# --- Disk-full abort ------------------------------------------------------
# The code of this section has ONE copy, in jxl_photo_common.py (AGENTS.md,
# "The common module"); the names below bind it here. Fix it there.
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


# Final paths whose move out of staging was refused this run (#615): the run
# exits 1 for them even though the conversion itself succeeded.
_swap_refusals = []


def _refresh_planned_output(path) -> None:
    """This run itself rewrote a planned output in place (the restamp, the
    adopt stamp): its record follows, or the later swap would refuse it."""
    key = os.path.normcase(os.path.abspath(str(path)))
    if key not in _planned_outputs:
        return
    try:
        st = os.stat(path)
    except OSError:
        _planned_outputs[key] = False
    else:
        _planned_outputs[key] = (st.st_size, st.st_mtime_ns)


def _flush_to_disk(*a, **k): return _common._flush_to_disk(_G, *a, **k)


def _reset_abort(*a, **k): return _common._reset_abort(_G, *a, **k)


def _aborted(*a, **k): return _common._aborted(_G, *a, **k)


def _signal_abort(*a, **k): return _common._signal_abort(_G, *a, **k)


def _abort_if_disk_full(*a, **k): return _common._abort_if_disk_full(_G, *a, **k)


def _promote_from_staging(*a, **k): return _common._promote_from_staging(_G, *a, **k)


# --- Directory-scan progress ----------------------------------------------
# The code of this section has ONE copy, in jxl_photo_common.py (AGENTS.md,
# "The common module"); the names below bind it here. Fix it there.
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
# The code of this section has ONE copy, in jxl_photo_common.py (AGENTS.md,
# "The common module"); the names below bind it here. Fix it there.
_STAGING_PREFIX_RE = _common._STAGING_PREFIX_RE
# A file still being written belongs to a run in flight, possibly a CONCURRENT
# one sharing this directory. Only sweep what has been sitting still a while.
_STAGING_MIN_AGE_SECONDS = 3600


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
_keep_alpha_flags = _common._keep_alpha_flags

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

_STREAMING_BYTES_PER_PIXEL = _common._STREAMING_BYTES_PER_PIXEL
_WHOLE_IMAGE_E7_BYTES_PER_PIXEL = _common._WHOLE_IMAGE_E7_BYTES_PER_PIXEL
_WHOLE_IMAGE_E8_BYTES_PER_PIXEL = _common._WHOLE_IMAGE_E8_BYTES_PER_PIXEL
_MODULAR_LOSSY_BYTES_PER_PIXEL = _common._MODULAR_LOSSY_BYTES_PER_PIXEL
_UNKNOWN_IMAGE_PIXELS = 60_000_000
# What each worker holds in THIS process on top of its cjxl (#569): the page
# array and the PNG handed to cjxl. Measured 2026-11-03 on 44.3 MP 16-bit RGB
# exports (Z8), python.exe private bytes, 1 -> 4 workers: +0.74 GB per worker
# at d=0 (16.8 B/px), +0.44 GB at d=0.1; +10% margin. Before the #569 fix it
# was +0.96 / +1.12 GB.
_PYTHON_BYTES_PER_PIXEL = 19


def _python_bytes_per_pixel(channels: int) -> int:
    """What each worker holds in Python per pixel for a page of `channels`
    samples: _PYTHON_BYTES_PER_PIXEL was measured on 3-channel pages, and
    the page array and the PNG for cjxl both grow with the channels. With
    the PNG at zlib level 0 (refactor B2) a 16-bit page of 4 channels (RGB+IR
    or RGBA) peaks at ~24.1 B/px, above the 3-channel 19 (#653, review V5)."""
    return math.ceil(_PYTHON_BYTES_PER_PIXEL * max(1, int(channels or 3)) / 3)


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
# 0    = lossless, pixel-perfect (~173 MB for 45 MP)
# 0.05 = finest lossy available (~42 MB)
# 0.1  = near-lossless (~31 MB) — the default and the recommendation for masters
# 0.5  = high-quality lossy (~11 MB), headroom for later edits
# 0.7  = visually lossless, keeps the grain texture (~7.8 MB)
# 1.0  = visually lossless, JPEG XL's definition (~5.5 MB)
# Sizes: medians of 20 Z7/Z8 photos; more in docs/file_sizes.md

CJXL_BUFFERING = None
# [libjxl >= 0.12 only] Encoder buffering level passed to cjxl.
# None (default) = do not pass the flag; cjxl chooses (-1): a streaming encode
#   (the fast, low-memory path — ~6x faster than 0 on large lossless TIFFs
#   with only ~1.2% larger files) EXCEPT at effort 7 with distance >= 3,
#   effort 8-9 with distance > 0.5 and effort 10+, where it encodes the whole
#   image at once (2.5-8x the RAM; see WORKER_MEMORY_FRACTION).
# 0 = buffer entire image = best compression / most RAM (restores pre-0.12
#     behavior; use for maximum density when encode time doesn't matter).
# 1-3 = always stream. On a lossy encode at effort 8-9 that gives the file
#     effort 7 would (cjxl 0.12, measured: docs/README_jxl_recompressor.md,
#     "Streaming vs whole-image"). Ignored automatically when cjxl is < 0.12
#     (flag doesn't exist there).

WORKER_MEMORY_FRACTION = 1.0
# Caps --workers so the parallel cjxl processes fit in memory. The peak of
# each worker is estimated from the largest image in the batch and the encode
# settings: cjxl encodes the whole image at once (2.5-8x the memory of its
# usual streaming encode) at effort 7 with distance >= 3, effort 8-9 with
# distance > 0.5, effort 10+, or --buffering 0. The run then uses at most this
# fraction of the memory WORKER_MEMORY_LIMIT picks (by default the smaller of
# what the system can still commit and the physical RAM still available).
# 0 disables the cap.

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
# Measured 2026-09 on 7 real photos/scans (SSIMULACRA2, d=0.05/0.10, e=7):
#       quality a wash (margins <= 0.39), VarDCT smaller in 14/14 (modular
#       up to +33%), VarDCT 20-100x faster. No photo use case for lossy
#       modular — see docs/README_jxl_tiff_encoder.md (CJXL_MODULAR).

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

ENCODE_TAG_MODE = "history"
# Every output records its encoding parameters as an append-only lineage
# chain: "gen=N | cjxl d=X e=Y | cjxl d=... e=..." (gen = number of LOSSY
# generations, reconciled from the chain on every write, never incremented).
# Since v2.24.0 the chain lives in the output's xmpMM:History entry (exiftool
# -XMP-xmpMM:HistoryParameters); this setting only decides where a VISIBLE
# copy goes:
# "history"  -> nowhere else (default): the Title stays the photo's own.
# "comment"  -> also XMP exif:UserComment, shown by Windows Explorer as
#              "Comments" (the Title stays the photo's own).
# "title"    -> also XMP-dc:Description, shown by Explorer as the Title
#              (the behaviour before v2.24.0; a caption stays FIRST).
# "software" -> also the EXIF Software field ("Capture One | gen=1 | cjxl d=0.5 e=7"),
#              visible in IrfanView.
# "xmp"      -> the old default's name: same as "history".
# "off"      -> records nothing (the History entry says "no encode record")
#              AND strips any chain the source TIFF carries — the only way to
#              deliberately discard the lineage.
# Files written before v2.24.0 keep the chain in dc:Description or Software;
# both scripts still read it there.

EMBED_ICC_IN_JXL = True
# Embeds the original ICC profile as metadata in the JXL file.
# The ICC is NOT used by the JXL decoder (JXL uses native primaries),
# but is preserved for round-trip conversion back to TIFF/JPEG.
# This ensures the exact original ICC (with TRC curves, copyright, etc.)
# is available when converting JXL -> TIFF, even for lossy JXLs.
# True  -> embed ICC profile in the JXL's XMP dc:Relation, as
#          "jxlphoto-iccprofile:<base64>" (recommended, default; up to
#          v2.18.x it went to XMP CreatorTool, which is still read)
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
# Controls the D50 illuminant patch: rewrites the ICC header's illuminant field
# (bytes 68-79) to the exact bytes of the ICC spec before cjxl sees the profile.
# Kodak's ProPhoto.icm (the ProPhoto RGB Capture One exports with) stores Z as
# 0xD32B instead of 0xD32D, 2/65536 off; no cjxl failure was ever traced to it.
# "on"   -> Always apply D50 patch
# "off"  -> Never apply D50 patch (use original ICC values)
# "auto" -> patch only a near-miss illuminant (e.g. Kodak's ProPhoto.icm,
#           Z 0xD32B); exact profiles and far-off ones are left as they are.
#           Decided from the ICC bytes; no extra exiftool call.
# Profiles that already carry the exact bytes come out unchanged in every mode.

CLEANUP_XMP_ICC_MARKER = False
# Remove legacy ICC markers from XMP if present.
# True  -> clears xmp-icc:all and xmp-photoshop:ICCProfile tags that might conflict
# False -> keeps existing ICC markers (default)

VERIFY_ROUNDTRIP = True
# Extra gate before DELETE_SOURCE removes a source TIFF: decode the JXL that
# was just written and compare it against the source page. Costs one full
# decode per output, so it roughly doubles a DELETING run — worth it exactly
# when the original is about to stop existing. On by default since v2.18.0
# (it used to be opt-in); runs that delete nothing never pay for it. False
# brings back the old, structure-only gate.
#
# _verify_jxl_integrity, which always runs, proves the file is a well-formed,
# untruncated container with a codestream. It does NOT prove the pixels came
# back. This does.
#
#   distance = 0  -> the decoded pixels must be IDENTICAL, or the source stays.
#   distance > 0  -> a sanity check, not a quality check: it answers "is this
#                    the same picture", not "is it a good encode". Quality loss
#                    is what you asked for; a BLACK frame is not. See the
#                    ICC_PNG_STRATEGY note above — some scanner profiles make
#                    cjxl emit near-black images that pass every structural
#                    check, and that is precisely the failure this catches.
#
# Has no effect without DELETE_SOURCE (it is a delete gate); a run that sets it
# alone says so and carries on. Requires djxl and imagecodecs.

VERIFY_LOSSY_MIN_MEAN_RATIO = 0.7
# [VERIFY_ROUNDTRIP, distance > 0] Decoded mean / source mean must stay within
# this factor in BOTH directions (0.7 -> between 0.7x and ~1.43x). Same constant
# and same reasoning as _CAU_MIN_RATIO in the cautious ICC test, which already
# uses a mean ratio to detect exactly this failure on a synthetic probe.

VERIFY_LOSSY_MIN_PSNR = 20.0
# [VERIFY_ROUNDTRIP, distance > 0] dB floor. Deliberately permissive: real
# photos land at 40-50 dB at d=1 and still 25-30 dB at d=15 (the highest
# distance this script accepts), while a different image or a decode that
# turned to noise sits at 5-15 dB. The gap is wide, so this rejects
# catastrophes without ever second-guessing a legitimate lossy encode.

DELETE_SKIPPED = False
# [DELETE_SOURCE only] Also delete sources whose output ALREADY EXISTS, i.e. the
# files this run reports as SKIP.
#
# Without this, an archive run can never be finished: if a delete failed once
# (locked file, antivirus, a run interrupted between the encode and the unlink),
# that TIFF is skipped on every later run — and a skip blocks the delete — so it
# sits there forever unless the whole library is re-encoded with --overwrite.
#
# The reason this is opt-in and gated harder than a normal delete: a SKIP proves
# far less than a conversion does. A conversion means "this run wrote the file
# AND it passed the integrity check". A skip means only "a file with that NAME
# exists and its mtime is not older than the source" — and mtime is not content.
# It says "newer" after a copy, a backup restore, a cloud sync, a touch, or on a
# filesystem with second granularity, and it says nothing at all about whether
# that JXL came from THIS photo.
#
# So a skipped source is never deleted on the strength of the timestamp. The
# output must still exist and pass _verify_jxl_integrity — the same structural
# floor a freshly converted file gets — and, with VERIFY_ROUNDTRIP, decode back
# to the source pixels. That last one matters MORE here than for a conversion:
# there is no "this run wrote it" to fall back on, so the pixel comparison is
# the only thing tying that JXL to that photo.
#
# Cost is self-limiting: the check only runs for sources that still exist, which
# is exactly the set still pending deletion. After one clean pass that is empty.

DELETE_SOURCE = False
# Whether to delete the source TIFF after successful encoding. Available in
# EVERY mode: the mode decides where the JXL lands, this decides whether the
# TIFF survives it.
# Only deletes if ALL of the following are true:
#   - encode status is ok or overwrite (never deletes on error or skip)
#   - no page of that TIFF was dropped by the multi-page policy
#   - the JXL exists at its FINAL destination (after the staging move, and the
#     move itself must have succeeded — a stale file already sitting there does
#     not count)
#   - it passes _verify_jxl_integrity there
#   - and, with VERIFY_ROUNDTRIP above, the decoded pixels match the source
#
# False (default) -> never delete source TIFFs.
# True            -> delete source TIFF after confirmed successful encode.
#
# WARNING: irreversible. Only enable after testing on a small batch first.
# Works in every mode. In modes that COLLAPSE folders (2 flattens a tree, 5
# merges siblings, 6/7 drop one level under the marker) what keeps this safe is
# _abort_on_duplicate_outputs: two sources mapping to one output abort the run
# before anything is written, rather than costing two originals.

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
#                a run that deletes refuses to delete a source whose pages
#                were dropped.
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

THUMBNAIL_MODE = "auto"
# Only used when MULTIPAGE_TIFF_MODE is "split".
# "auto"    -> [DEFAULT] Encode thumbnail pages, except for TIFFs whose Software
#              tag names a program in THUMBNAIL_DROP_SOFTWARE (Capture One puts
#              a tiny preview in every export).
# "scans"   -> Encode thumbnail pages only for scans: the Software tag names a
#              program in SCANNER_SOFTWARE, or a page is an IR/mask page
#              (SubfileType 4).
# "exclude" -> Do not encode thumbnail pages (the default before v2.17.0).
# "include" -> Encode thumbnail pages too, with a _thumbnail suffix.
#
# A thumbnail is a REDUCED-resolution copy of another page (TIFF SubfileType
# is_reduced / is_subifd), i.e. derived data — which is why excluding it does not
# block source deletion in mode 8, while dropping a real page does. Scanner
# software can still depend on it: SilverFast shows "Loading..." instead of the
# picture in its browser for a scan that lost its thumbnail page.
#
# "auto"/"scans" never add a thumbnail to an archive this run does not rewrite:
# a TIFF whose main JXL is skipped (sync: up to date, or no --overwrite) keeps
# its archived pages, so a split never gets a lone new page beside an older
# group. Run once with --overwrite on those folders to add the thumbnails.

THUMBNAIL_DROP_SOFTWARE = (
    "Capture One",
)
# "auto" leaves out the thumbnail page of a TIFF whose Software tag CONTAINS
# one of these (case-insensitive). Add the programs whose embedded previews you
# do not want as _thumbnail.jxl files.

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
# A TIFF whose Software tag CONTAINS one of these (case-insensitive) — or that
# has an IR page (a mask page, or a 2nd/4th channel that is not alpha), or
# that has NEITHER a Software tag NOR camera EXIF (exposure, aperture, focal
# length: lab scans and Epson Scan write no Software at all) — is a FILM SCAN:
# SCAN_DISTANCE applies to it, and --thumbnail-mode scans keeps its thumbnail
# page. Add your scanner's program. That last case is a guess, and the run says
# so (its folders, before the first encode); a TIFF without either that carries
# a 160x120 thumbnail page — the one cameras write — is taken for a camera TIFF
# that lost its tags, not a scan.

SCAN_DISTANCE = 0.0
# Distance for film scans (also --scan-distance). 0 = lossless (default): a
# scan is an original that can only be redone by scanning again, a "final"
# scan is often the only copy left, and the heavy processing a raw negative
# gets later (inversion, curves) magnifies a loss that was invisible in the
# file. None = scans use CJXL_DISTANCE like every other TIFF.

SCAN_SCOPE = "all"
# Which scans SCAN_DISTANCE covers (also --scan-scope):
# "all" -> every scan (default).
# "raw" -> raw scans only: the scanner's own ICC profile (class 'scnr'), no
#          profile at 16 bits, or an IR page. A "final" scan (converted to an
#          output profile such as sRGB or Adobe RGB) then uses CJXL_DISTANCE.

EIGHT_BIT_DISTANCE = "auto"
# Distance for 8-bit TIFFs that are not film scans (also --eight-bit-distance).
# An 8-bit TIFF is small lossless (about 43 MB for 45 MP, a quarter of a
# 16-bit one) and d=0.05 comes out LARGER than that (docs/file_sizes.md):
# "auto"   -> lossless when the run's distance is EIGHT_BIT_LOSSLESS_UP_TO or
#             less, the run's distance above it (default);
# "same"   -> the run's distance, like any other TIFF;
# a number -> that distance for every 8-bit TIFF (0 = always lossless).
# Film scans keep SCAN_DISTANCE.

EIGHT_BIT_LOSSLESS_UP_TO = 0.1
# The highest run distance at which EIGHT_BIT_DISTANCE = "auto" still
# archives an 8-bit TIFF lossless. At 0.1 lossy is only ~24% smaller than
# lossless (33 vs 43 MB at 45 MP); at 0.2 it is half.

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

ICC_PROFILE_PREFIX = _common.ICC_PROFILE_PREFIX
# "jxlphoto-iccprofile:<base64>" in dc:Relation: the ORIGINAL ICC profile,
# byte for byte (EMBED_ICC_IN_JXL). Must match the other scripts'. Up to
# v2.18.x it lived in XMP CreatorTool as "ICC:<base64>", where every viewer
# that shows the creating program printed it; the readers still accept that
# form, and an output written from such a file moves it here.

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

EXTRASAMPLES_XMP_PREFIX = "jxlphoto-extrasamples:"
# The page's TIFF ExtraSamples value(s) when it has a 2nd/4th channel
# (0 = unspecified, e.g. a scanner's IR channel; 1 = associated alpha;
# 2 = unassociated alpha), comma-separated. JPEG XL stores that channel as
# alpha and keeps no TIFF role for it, so without this the decoder wrote every
# such channel back as unassociated alpha — an IR channel came back as a
# transparency mask (D3 of the 2026-10-08 audit).

SCAN_XMP_PREFIX = "jxlphoto-scan:"
# "jxlphoto-scan:raw" / "jxlphoto-scan:final" on every page of a film scan
# (_scan_kind): what the recompressor's --on-scan guard reads, since the facts
# behind it (the TIFF's Software tag, ICC class, IR page) are not all visible
# in one JXL later. Re-derived from the TIFF on every encode.
# "jxlphoto-scan:no" on a TIFF with neither a Software tag nor camera EXIF that
# _scan_reason took for a camera TIFF (its 160x120 thumbnail page): without
# it the recompressor would take the master for an old lab scan.

TIFFTAG_XMP_PREFIX = "jxlphoto-tifftag:"
# A PRIVATE TIFF tag of the page (code >= 32768) that exiftool cannot copy:
# "jxlphoto-tifftag:<code>:<type>:<hex bytes>", one per tag. SilverFast writes
# two (35243, 35244: an id and the film name); the decoder writes them back on
# the same page. Only byte-sized types (BYTE, ASCII, SBYTE, UNDEFINED) — a
# LONG can be a file offset, meaningless in a new file — and only when every
# such tag of the page fits PRIVATE_TAG_MAX_BYTES: Affinity Photo stores a
# 110-byte header beside its 230 MB document, and one without the other is
# worse than neither.
PRIVATE_TAG_MAX_BYTES = 4096
# Private tags exiftool or this pipeline already carries, or that hold offsets.
_PRIVATE_TAG_SKIP = frozenset({
    33432,                          # Copyright (exiftool copies it)
    #   33723 (IPTC) is NOT here since v2.27.0: exiftool copies it TIFF -> TIFF
    #   but a JXL has no IPTC box, so it is carried as a token of its own
    #   (_private_tiff_tags: any of BYTE/ASCII/SBYTE/UNDEFINED/LONG, written
    #   back as UNDEFINED, its own size limit) and its fields are also copied
    #   into the XMP (IPTC_TO_XMP_FILL).
    #   34377 (Photoshop image resources) is NOT here since v2.28.0: carried
    #   like the IPTC block, judged apart, when it fits the limit
    #   (_private_tiff_tags, _photoshop_left_out_note).
    34665, 34853, 40965,            # Exif / GPS / Interop IFD pointers
    34675,                          # ICC profile
    37724,                          # Photoshop ImageSourceData (layers)
    40091, 40092, 40093, 40094, 40095,  # Windows XP* tags (exiftool copies them)
    50341,                          # PrintIM
    50740,                          # DNGPrivateData (holds offsets)
    59932,                          # Padding: zeros Windows Photos reserves for
                                    # edits in place (2 KB of nothing, a 4 KB token)
    59933,                          # OffsetSchema (an offset, meaningless in a new file)
})
_PRIVATE_TAG_TYPES = frozenset({1, 2, 6, 7})   # BYTE, ASCII, SBYTE, UNDEFINED

_PHOTOSHOP_TAG = 34377
# The Photoshop image resources of a TIFF ("8BIM" records: a caption digest,
# print settings, slices, clipping paths, a preview, sometimes the IPTC).
# exiftool does not copy it into a JXL, so it is carried as a token like any
# private tag when it fits PRIVATE_TAG_MAX_BYTES and left out when it does
# not (a Lightroom or Photoshop TIFF: most of the block is a JPEG preview).
# Judged apart from the program's own tags, like the IPTC block: its size is
# never a reason to keep the TIFF (#620 decided that for this tag), and an
# oversized tag of another program never costs it.
_IRB_IPTC = 0x0404                  # the IPTC (IIM) stream inside the block
_IRB_PATHS = range(2000, 2999)      # saved paths, the clipping path among them

_IPTC_TAG = 33723
# The IPTC block (IIM) of a TIFF: exiftool itself writes it as a LONG array
# whose bytes are the IIM stream as is, in either byte order (measured
# 2026-11-11, little- and big-endian files). Unlike every other private tag it
# is a byte stream whatever its declared type, so LONG is accepted for it and
# its token is written as UNDEFINED (7) — the decoder accepts only 1/2/6/7 and
# the payload is the same.
_IPTC_TAG_TYPES = frozenset({1, 2, 4, 6, 7})

# IPTC field -> XMP field, in exiftool's names. At encode, each is copied into
# the master's XMP ONLY IF the XMP does not already hold it (-wm cg:
# _fill_xmp_from_iptc), so the capture date and the descriptive fields of a
# file that kept them in the IPTC alone (SilverFast scans) are not lost. Not a
# user setting: a mapping. Urgency, CodedCharacterSet, ApplicationRecordVersion,
# DigitalCreation* and TimeCreated are left out on purpose (editor/camera
# noise; the exact block comes back from the token).
IPTC_TO_XMP_FILL = (
    ("Keywords", "XMP-dc:Subject"),
    ("Caption-Abstract", "XMP-dc:Description"),
    ("By-line", "XMP-dc:Creator"),
    ("ObjectName", "XMP-dc:Title"),
    ("CopyrightNotice", "XMP-dc:Rights"),
    ("Headline", "XMP-photoshop:Headline"),
    ("City", "XMP-photoshop:City"),
    ("Province-State", "XMP-photoshop:State"),
    ("Country-PrimaryLocationName", "XMP-photoshop:Country"),
    ("Country-PrimaryLocationCode", "XMP-iptcCore:CountryCode"),
    ("Sub-location", "XMP-iptcCore:Location"),
    ("Credit", "XMP-photoshop:Credit"),
    ("Source", "XMP-photoshop:Source"),
    ("SpecialInstructions", "XMP-photoshop:Instructions"),
    ("DateCreated", "XMP-photoshop:DateCreated"),
)

DEPTH_XMP_PREFIX = "jxlphoto-depth:"
# Prefix for the original BitsPerSample value stored in dc:Relation. The decoder
# uses this to restore the original bit depth per page according to the user's
# --depth-policy (force16 / preserve_thumbnails / preserve_original).

PAGE_XMP_PREFIX = "jxlphoto-page:"
# Prefix for the TIFF page index stored in dc:Relation on split pages. Without
# it the decoder infers the page index from the FILENAME, which breaks when the
# source TIFF itself is named *_page<N> or *_thumbnail.

PAGES_XMP_PREFIX = "jxlphoto-pages:"
# How many JXLs this split produced, written on every page of the group. The
# page index alone cannot tell a COMPLETE group from a truncated one: pages
# {0,1} of a three-page scan look exactly like a two-page scan, and the decoder
# would write a valid two-page TIFF and let --delete-source destroy the JXLs it
# did find — the third page then exists nowhere. The count is the only thing
# that closes that hole.
#
# It counts the JXLs WRITTEN, not the pages the source TIFF had: a thumbnail
# dropped by --thumbnail-mode exclude is not part of the group, so the decoder
# must not go looking for it. Archives written before this marker existed carry
# none, and the decoder treats "no count" as "cannot tell" rather than
# "incomplete" — see _run_collapses_structure's comment for why refusing what
# you cannot verify is the wrong default when the archive is already out there.

THUMB_XMP_FLAG = "jxlphoto-thumb"
# Marker appended to dc:Relation on thumbnail pages of a split (instead of
# relying on the _thumbnail filename suffix).

SRC_PREFIX = "jxlphoto-src:"
SRCSUM_PREFIX = "jxlphoto-srcsum:"
ORIGIN_PREFIX = _common.ORIGIN_PREFIX
_TOOLKIT_MARKER_NAMESPACE = _common._TOOLKIT_MARKER_NAMESPACE
# An encode record anywhere in Description/Software: the input came from this
# toolkit (an older version, before the origin marker existed).
_ORIGIN_RECORD_RE = re.compile(r"(?:^|\|)\s*cjxl d=", re.MULTILINE)
DERIVED_XMP_PREFIX = "jxlphoto-derived:"
# The recipe marker the recompressor/transcoder write on a DERIVATIVE (colour
# conversion, resize, sharpening). Must match theirs. Never copied onward:
# a file decoded from a derivative and encoded again is a NEW master, and a
# master carrying it reads as a derivative to their "only overwrite our own
# derivatives" guard (E8/D4 of the 2026-10-08 audit).
# WHICH source this output was made from. Both are always written (when metadata
# is not stripped): the path id is free, and the content id is hashed from the
# pixel array that is already in memory, so recording both costs almost nothing
# and lets PROVENANCE_CHECK be changed later without re-encoding the archive.
#
# They exist because _abort_on_duplicate_outputs only sees collisions WITHIN one
# run. In the modes that collapse folder structure (2/4/5/6/7) a file added
# later, in a different folder, resolves to the SAME output path — and with
# --delete-source that silently destroyed the earlier photo: its source was
# deleted by the first run and its archive overwritten by the second.

ADOPT_SCAN = True
# [PROVENANCE_CHECK = "adopt" only] Prove the pairing before adopting an output
# that carries no marker, by decoding it and comparing with the source. True is
# what makes "adopt" safe rather than a rubber stamp. Turning it off
# (--no-adopt-scan) is much faster on a large library and only defensible when
# you know the archive was never built with a folder-collapsing mode that could
# have put a different photo under that name.

PROVENANCE_CHECK = "path"
# How an EXISTING output is matched against the source about to overwrite it.
# Two levels:
#   * --delete-source in a mode that collapses folders: EVERY existing output
#     must prove it came from this source (a markerless one included — that is
#     what "adopt" heals);
#   * every other run: an output that is about to be OVERWRITTEN and whose
#     marker names a DIFFERENT source is refused (a markerless one is
#     overwritten as before). "Without deletion an overwrite is recoverable"
#     only holds for the NEW source: the old archive's own source may have been
#     deleted by an earlier run, and then the overwrite is the loss (#268's
#     missing half). Every mode collapses names, too: foto.tif and foto.tiff
#     both write foto.jxl.
#
# "path"    -> (default) the archive must record THIS source path. Cheap: no
#              file is read. Survives re-editing and re-exporting a file in
#              place, which is the normal sync workflow. Does NOT survive moving
#              the source folder — that reads as a different file and is refused
#              (safely: the source is kept, nothing is overwritten).
# "content" -> accepts a matching path OR a matching image. Reads and hashes the
#              source page, so it is slower, but it survives moved folders.
#              Deliberately a SUPERSET of "path": content alone would refuse a
#              legitimately re-edited file, breaking the sync workflow.
#
# Either way a mismatch is fail-CLOSED: the file is not converted, the existing
# output is not touched, and no source is deleted.

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
EXCLUDE_FOLDERS: tuple = ()   # lower-cased folder names excluded from discovery
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
# Only the encode record (gen=N | cjxl d=X e=Y) is kept — its History entry
# and the visible copy of ENCODE_TAG_MODE; with "off", not even that.
# Useful for creating clean JXL files without embedded metadata.
# Can also be set via --strip CLI flag.

EXPLORER_THUMBNAIL = "auto"
# A small sRGB JPEG of the image stored inside the JXL, which jxl-thumb
# (https://github.com/rsilvabr/jxl-thumb, 0.2.0+) shows in Windows Explorer
# instead of decoding the photo. Also --explorer-thumbnail.
# "auto" (default) -> every output when jxl-thumb is installed for this
#          Windows user (it is what shows them), lossless outputs only
#          otherwise: a lossless JXL has no small preview inside, so its
#          thumbnail means decoding the whole file (1.8 s per 45 MP photo on
#          a hard disk, against ~2 ms); a lossy one keeps a 1:8 preview of its
#          own (~50-75 ms).
# "on"   -> every output. "off" -> none.
# Stored in a box of the JXL container that other programs skip (not in the
# EXIF, where it would break djxl's JPEG output). ~90 KB per file; ~2 s per
# 45 MP lossless encode, ~1.2 s per lossy one (djxl + magick). Needs djxl and
# ImageMagick; without them the output is written without it. Never written
# with --strip, never into a small file (EXPLORER_THUMBNAIL_MAX_SHARE).

EXPLORER_THUMBNAIL_SIZE = 768
# Long side in pixels. 768 is the largest thumbnail Explorer asks for its icon
# views (extra large icons); bigger requests (preview pane) use the JXL itself.

EXPLORER_THUMBNAIL_MAX_SHARE = 0.10
# Small files get none: an image no larger than EXPLORER_THUMBNAIL_SIZE, or a
# thumbnail that would be more than this share of the file (an 800 px d=3 JXL
# of 50 KB, a 2048 px derivative). Those decode fast anyway. A 45 MP master's
# thumbnail is 0.05-1% of it.

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
    "CJXL_EFFORT": ("int", 1, 10),
    "CJXL_DISTANCE": ("float", 0.0, 25.0),
    "CJXL_BUFFERING": ("choice", None, 0, 1, 2, 3),
    "CJXL_MODULAR": ("bool",),
    "CJXL_TIMEOUT": ("int", 1, None),
    "EXIFTOOL_TIMEOUT": ("int", 1, None),
    "EXIFTOOL_BATCH_STALL": ("int", 1, None),
    "EXIFTOOL_BATCH_TIMEOUT": ("int", 1, None),
    "TEMP_DIR": ("path?",),
    "TEMP2_DIR": ("path?",),
    "WORKER_MEMORY_FRACTION": ("float", 0.0, 2.0),   # 0 = no cap; above 1 trusts the estimate
    "WORKER_MEMORY_LIMIT": ("choice", "both", "commit", "physical"),
    "USE_RAM_FOR_PNG": ("bool",),
    "ENCODE_TAG_MODE": ("choice", "history", "comment", "title", "xmp", "software", "off"),
    "ICC_PNG_STRATEGY": ("choice", "heuristic", "always", "skip", "cautious"),
    "ICC_PROBLEMATIC_SIZE_THRESHOLD": ("int", 0, None),
    "D50_PATCH_MODE": ("choice", "on", "off", "auto"),
    "CLEANUP_XMP_ICC_MARKER": ("bool",),
    "MULTIPAGE_TIFF_MODE": ("choice", "ignore", "skip", "split", "split_all"),
    "THUMBNAIL_MODE": ("choice", "auto", "scans", "exclude", "include"),
    "THUMBNAIL_DROP_SOFTWARE": ("strs",),
    "SCANNER_SOFTWARE": ("strs",),
    "SCAN_DISTANCE": ("float?", 0.0, 25.0),
    "SCAN_SCOPE": ("choice", "all", "raw"),
    "THUMBNAIL_SUFFIX": ("name",),
    "WARN_DISCARDED_THUMBNAILS": ("bool",),
    "CONVERTED_JXL_FOLDER": ("name",),
    "JXL_FOLDER_NAME": ("name",),
    "TIFF_SUFFIX_TO_REPLACE": ("name",),
    "JXL_SUFFIX_REPLACE": ("name",),
    "EXPORT_MARKER": ("name",),
    "EXPORT_JXL_FOLDER": ("name",),
    "EXPORT_TIFF_SUBFOLDER": ("name?",),
    "EXPLORER_THUMBNAIL": ("choice", "auto", "on", "off"),
    "EXPLORER_THUMBNAIL_SIZE": ("int", 64, 2048),
    "EXPLORER_THUMBNAIL_MAX_SHARE": ("float", 0.0, 1.0),
}
# Settings computed from another one: they follow it (see _apply_settings_overlay).
_OVERLAY_DERIVED = {"EXIFTOOL_TIMEOUT": "CJXL_TIMEOUT"}
_SETTINGS_OVERLAY = _apply_settings_overlay(globals(), "jxl_tiff_encoder", _OVERLAY_SETTINGS,
                                            _OVERLAY_DERIVED)

SCRIPT_DIR = Path(__file__).parent
# JXLPHOTO_LOG_DIR (environment variable) moves the log folders elsewhere —
# the test suite and the real-photo battery set it so their runs never
# land in Logs\ next to the scripts.
LOG_DIR = (Path(os.environ["JXLPHOTO_LOG_DIR"]) if os.environ.get("JXLPHOTO_LOG_DIR")
           else SCRIPT_DIR / "Logs") / Path(__file__).stem
counter_lock = threading.Lock()
_counter = {"done": 0, "total": 0}
_d50_patch_count = {"applied": 0, "skipped": 0, "already_correct": 0, "skipped_needed": 0,
                    "applied_already_correct": 0, "skipped_already_correct": 0,
                    "other_illuminant": 0}
# Unique ICC profiles patched (by md5 of the original ICC bytes). The counters
# above increment per page in multipage splits; this set deduplicates profiles.
_d50_patched_hashes = set()
_d50_patch_lock = threading.Lock()
# Pages silently dropped by --multipage-mode ignore. Counted so the run summary
# can say it out loud: per-file warnings scroll away in a large batch.
_multipage_ignored = {"files": 0, "pages": 0}
_multipage_ignored_lock = threading.Lock()
# Pages whose Photoshop image resources were too big to carry
# (_photoshop_left_out_note): one line in the summary, under the same lock.
_photoshop_left_out = {"files": 0}
# Thumbnail pages dropped WITHOUT their real page being dropped too — by
# --multipage-mode split + --thumbnail-mode exclude, and by --multipage-mode
# skip, which encodes the single real page and silently left its embedded
# preview behind. Tracked SEPARATELY from _multipage_ignored: mixing them made
# the summary blame "--multipage-mode ignore" for a drop that happened
# elsewhere.
# "archived": of those files, how many kept their archived layout under
# --thumbnail-mode auto/scans because this run does not rewrite their main JXL
# (see _thumbnail_sync_guard).
_thumbnails_dropped = {"files": 0, "pages": 0, "archived": 0}
# Source TIFFs that lost REAL image pages at planning time (--multipage-mode
# ignore). Mode 8 consults this before deleting: encoding page 0 and then
# unlinking a 3-page scan destroys the other two pages permanently, and no
# integrity check downstream can notice — the one JXL that was written is
# perfectly valid. Keyed by os.path.normcase(str(tiff_path)).
_discarded_real_page_sources = set()
# Source TIFFs holding content no JXL can carry (#620): Photoshop layers
# (tag 37724) or a private tag too large to carry. The JXL keeps the
# flattened image, so --delete-source must keep the TIFF.
# normcase(str(tiff_path)) -> what it holds.
_unarchived_content_sources = {}
_LAYERS_TAG = 37724
# Same, for thumbnail pages only. These do NOT block deletion — a thumbnail is a
# reduced-resolution copy of a page that IS in the output — but the delete line
# says so, because the decoded TIFF will not have that preview page back.
_discarded_thumb_sources = set()
# Per-file discard warnings are capped: a 5000-file library must not push every
# real error off the screen. The full totals always land in the run summary.
DISCARD_WARN_LIMIT = 20
_discard_warned = {"count": 0, "suppressed": 0}
# What the deletion actually did. Module-level because process_group owns the
# delete gate but main() owns the summary — without this the most destructive
# thing the tool does never reached emit_summary_json, so the wrapper's
# end-of-manifest recap could not say a single word about it.
_delete_stats = {"deleted": 0, "deleted_archived": 0, "kept": 0}

# Script defaults for every global the CLI mutates, captured at import. main()
# resets run-scoped state UNCONDITIONALLY at entry (a second in-process run
# used to inherit the first one's armed --delete-source / --delete-confirm-off
# / --staging — the flags were assigned one-way, `if args.x:` with no else;
# the recompressor got exactly this reset in the same round). The reset must
# restore THESE values and not hardcoded literals: a user who edits a setting
# in the file must not have it silently revert at the top of every run; the
# `if args.x:` overrides then apply per flag below. (EXCLUDE_FOLDERS is not
# here only because it is rewritten unconditionally from --exclude-folders a
# few lines into main(), so it cannot leak between runs as-is.)
_RUN_DEFAULTS = {
    "OVERWRITE": OVERWRITE,
    "CJXL_DISTANCE": CJXL_DISTANCE,
    "CJXL_EFFORT": CJXL_EFFORT,
    "CJXL_BUFFERING": CJXL_BUFFERING,
    "CJXL_MODULAR": CJXL_MODULAR,
    "USE_RAM_FOR_PNG": USE_RAM_FOR_PNG,
    "DELETE_SOURCE": DELETE_SOURCE,
    "DELETE_CONFIRM": DELETE_CONFIRM,
    "TEMP2_DIR": TEMP2_DIR,
    "ENCODE_TAG_MODE": ENCODE_TAG_MODE,
    "D50_PATCH_MODE": D50_PATCH_MODE,
    "MULTIPAGE_TIFF_MODE": MULTIPAGE_TIFF_MODE,
    "THUMBNAIL_MODE": THUMBNAIL_MODE,
    "THUMBNAIL_DROP_SOFTWARE": THUMBNAIL_DROP_SOFTWARE,
    "SCANNER_SOFTWARE": SCANNER_SOFTWARE,
    "SCAN_DISTANCE": SCAN_DISTANCE,
    "SCAN_SCOPE": SCAN_SCOPE,
    "EIGHT_BIT_DISTANCE": EIGHT_BIT_DISTANCE,
    "EIGHT_BIT_LOSSLESS_UP_TO": EIGHT_BIT_LOSSLESS_UP_TO,
    "THUMBNAIL_SUFFIX": THUMBNAIL_SUFFIX,
    "WARN_DISCARDED_THUMBNAILS": WARN_DISCARDED_THUMBNAILS,
    "ICC_PNG_STRATEGY": ICC_PNG_STRATEGY,
    "ICC_CACHE_DIR_OVERRIDE": ICC_CACHE_DIR_OVERRIDE,
    "VERIFY_ROUNDTRIP": VERIFY_ROUNDTRIP,
    "DELETE_SKIPPED": DELETE_SKIPPED,
    "PROVENANCE_CHECK": PROVENANCE_CHECK,
    "OVERWRITE_GUARD": OVERWRITE_GUARD,
    "ADOPT_SCAN": ADOPT_SCAN,
    "EXPORT_TIFF_SUBFOLDER": EXPORT_TIFF_SUBFOLDER,
    "EXPORT_JXL_FOLDER": EXPORT_JXL_FOLDER,
    "EXPORT_MARKER": EXPORT_MARKER,
    "STRIP_METADATA": STRIP_METADATA,
    "EXPLORER_THUMBNAIL": EXPLORER_THUMBNAIL,
}

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


def _record_dropped_thumbnails(tiff_path, pages: int, archived: bool = False):
    """Book one TIFF whose thumbnail page(s) this run chose not to encode.

    Shared by the `split` and `skip` planners so the two can never drift again:
    `skip` used to drop the embedded preview of every single-real-page TIFF
    without touching a counter, so nothing said so — not the run summary, not
    the wrapper's `extras`, and not the mode-8 delete line, which is the one
    place that warns the decoded TIFF will come back without that page.

    archived: the thumbnail was left out only to keep an existing archive's
    layout (_thumbnail_sync_guard), not because the mode drops it.
    """
    if pages <= 0:
        return
    with _multipage_ignored_lock:
        _thumbnails_dropped["files"] += 1
        _thumbnails_dropped["pages"] += pages
        if archived:
            _thumbnails_dropped["archived"] = _thumbnails_dropped.get("archived", 0) + 1
        _discarded_thumb_sources.add(os.path.normcase(str(tiff_path)))
        if WARN_DISCARDED_THUMBNAILS:
            _capped_discard_warning(
                f"DISCARDING {pages} thumbnail page(s) | {tiff_path.name} | "
                f"--multipage-mode {MULTIPAGE_TIFF_MODE} does not encode them")


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
        # Name the setting that actually caused it. --thumbnail-mode is only
        # consulted in split, so pointing a `skip` run at it would advertise a
        # fix that changes nothing.
        _tm = THUMBNAIL_MODE.lower()
        if MULTIPAGE_TIFF_MODE.lower() == "skip":
            hint = ("--multipage-mode skip encodes the single real page only). "
                    "Use --multipage-mode split_all to encode them too.")
        elif _tm == "auto":
            hint = ("--thumbnail-mode auto: their Software is in "
                    "THUMBNAIL_DROP_SOFTWARE). Use --thumbnail-mode include to "
                    "encode them too.")
        elif _tm == "scans":
            hint = ("--thumbnail-mode scans: not recognised as scans — no "
                    "SCANNER_SOFTWARE name, no IR page). Use --thumbnail-mode "
                    "include to encode them too.")
        else:
            hint = ("--thumbnail-mode exclude, as requested). "
                    "Use --thumbnail-mode include to encode them too.")
        _archived = _thumbnails_dropped.get("archived", 0)
        if _archived and _archived == _thumbnails_dropped["files"]:
            hint = ("their archives were written without them and this run did "
                    "not rewrite those). Run once with --overwrite to add them.")
        elif _archived:
            hint += (f" {_archived} of these TIFF(s) only kept the layout of an "
                     f"archive this run did not rewrite; --overwrite adds theirs.")
        logger.info(
            f"Thumbnails: excluded {_thumbnails_dropped['pages']} thumbnail page(s) from "
            f"{_thumbnails_dropped['files']} TIFF(s) ({hint}")

    if _discard_warned["suppressed"]:
        logger.info(f"({_discard_warned['suppressed']} per-file discard warning(s) suppressed; "
                    f"see the totals above)")


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


def _abort_on_duplicate_outputs(*a, **k): return _common._abort_on_duplicate_outputs(_G, *a, **k)

def setup_logger(*a, **k): return _common.setup_logger(_G, *a, **k)


_utf8_console = _common._utf8_console


# Machine-readable run summary for the jxl_photo.py wrapper.
#
# A manifest run spawns one child PER ENTRY, each writing its own log file, so
# the wrapper had no way to total a multi-entry run: the user saw only the last
# entry's "Done:" line and had to open N logs to find out whether anything
# failed. The wrapper consumes this line and does NOT print it.
#
# Gated behind --summary-json so a direct human run never sees the JSON. Keep
# the key names stable: jxl_photo.py parses them.
SUMMARY_PREFIX = _common.SUMMARY_PREFIX

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
        # Modes 3/4/5 accept a single FILE as input_root. Testing the output
        # against the file itself can never pass (a path is never "under" a
        # file), and testing against the file's parent still flags EVERY
        # legitimate single-file mode-4/5 run: those modes write a SIBLING of
        # the file's folder by design. The tree such a run must stay inside is
        # the folder holding that sibling pair — so anchor at the file's
        # parent folder's parent.
        anchor = input_root.parent.parent if input_root.is_file() else input_root
        if result is not None and not _is_relative_to(result, anchor):
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
        export_idx = _marker_anchor_index(parts[:-1], input_root, marker_lower)
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
        export_idx = _marker_anchor_index(parts[:-1], input_root, marker_lower)
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
# Does this exiftool run several commands in one process with a status
# each? {exiftool command: bool}, asked once (_exiftool_batch_ok).
_EXIFTOOL_BATCH = {}
# Read-only exiftool results run ahead in one process, per thread
# (_prefetch_exiftool_reads), each used once by the read that asks for it.
_EXIFTOOL_PREFETCH = threading.local()


# Captured at import: some tests replace this module's `subprocess` with a stub.
_DEVNULL = subprocess.DEVNULL
_CompletedProcess = subprocess.CompletedProcess


_LONG_PATH_THRESHOLD = _common._LONG_PATH_THRESHOLD


_long_codec_paths = _common._long_codec_paths


_ansi_encodable = _common._ansi_encodable


def _argfile_via_stdin(*a, **k): return _common._argfile_via_stdin(_G, *a, **k)


def _run_captured(*a, **k): return _common._run_captured(_G, *a, **k)


def _run_to_memory(*a, **k): return _common._run_to_memory(_G, *a, **k)


_stderr_tail = _common._stderr_tail


def _exiftool_source(*a, **k): return _common._exiftool_source(_G, *a, **k)


def _run_exiftool_argfile(*a, **k): return _common._run_exiftool_argfile(_G, *a, **k)


def _exiftool_batch(*a, **k): return _common._exiftool_batch(_G, *a, **k)


_RELATION_BATCH_HEAD = _common._RELATION_BATCH_HEAD


def _run_exiftool_writes(*a, **k): return _common._run_exiftool_writes(_G, *a, **k)


def _retarget_exiftool_writes(writes, target, stub):
    """The encoder's exiftool writes (command, failure text, quoted output)
    aimed at `stub` instead of `target` (#675), or None when a command does
    not name `target` exactly once — the writes then run on `target` as they
    are. An argfile command is COPIED with its target line replaced (bytes,
    line ends kept), never edited: when the stub route is not taken, the
    original writes still aim at the master."""
    t, s = str(target), str(stub)
    tb, sb = t.encode("utf-8"), s.encode("utf-8")
    out = []
    for cmd, msg, quoted in writes:
        if isinstance(cmd, Path):
            lines = cmd.read_bytes().split(b"\n")
            hits = [i for i, line in enumerate(lines) if line.rstrip(b"\r") == tb]
            if len(hits) != 1:
                return None
            i = hits[0]
            lines[i] = sb + (b"\r" if lines[i].endswith(b"\r") else b"")
            copy = cmd.with_name(cmd.stem + "_stub" + cmd.suffix)
            copy.write_bytes(b"\n".join(lines))
            out.append((copy, msg, quoted))
        else:
            args = [str(a) for a in cmd]
            if args.count(t) != 1:
                return None
            out.append(([s if a == t else a for a in args], msg, quoted))
    return out


def _prefetch_exiftool_reads(*a, **k): return _common._prefetch_exiftool_reads(_G, *a, **k)


def _clear_exiftool_prefetch(*a, **k): return _common._clear_exiftool_prefetch(_G, *a, **k)


_origin_read_args = _common._origin_read_args


def _tiff_byte_order(tiff_path) -> str:
    """'Big-endian' for a Motorola (MM) TIFF, else 'Little-endian' — the
    ExifByteOrder value that keeps the master's EXIF in the TIFF's own byte
    order (v2.25.0). Read from the file's first two bytes; an unreadable
    header gives the TIFF default, little-endian."""
    try:
        with open(tiff_path, "rb") as f:
            return "Big-endian" if f.read(2) == b"MM" else "Little-endian"
    except OSError:
        return "Little-endian"


def extract_exif_raw(tiff_path, tmp_dir):
    arg_file = tmp_dir / "exif_extract.args"
    arg_file.write_text(f"{_ARGFILE_CHARSET}-b\n-Exif\n{tiff_path}\n", encoding="utf-8")
    r = _run_captured([_get_exiftool_cmd(), "-@", str(arg_file)], EXIFTOOL_TIMEOUT)
    if r.returncode == 0 and r.stdout and len(r.stdout) > 8:
        # Never the source's name: a '%' in it would be expanded by the
        # -Exif<= argument that reads this file back (#550).
        p = tmp_dir / "src.exif.bin"
        p.write_bytes(r.stdout)
        return p
    return None

def _is_d50_already_correct(icc_bytes: bytes) -> bool:
    """Check if ICC profile already has correct D50 illuminant values.
    Returns True if D50 bytes are already correct (no patch needed)."""
    if len(icc_bytes) < 80:
        return False
    CORRECT_D50 = bytes.fromhex("0000f6d6000100000000d32d")
    return icc_bytes[68:80] == CORRECT_D50

_D50_SPEC = (63190, 65536, 54061)   # 0000F6D6 00010000 0000D32D
_D50_NEAR_MISS_UNITS = 4

def _d50_near_miss(icc_bytes: bytes) -> bool:
    """True when the header illuminant is within _D50_NEAR_MISS_UNITS
    (1/65536 each) of the spec D50 on X, Y and Z but not exact - Kodak's
    ProPhoto.icm stores Z as 0xD32B. Far-off values are not D50 attempts
    and are left alone by "auto"."""
    if len(icc_bytes) < 80 or _is_d50_already_correct(icc_bytes):
        return False
    vals = struct.unpack(">iii", bytes(icc_bytes[68:80]))
    return all(abs(v - s) <= _D50_NEAR_MISS_UNITS for v, s in zip(vals, _D50_SPEC))

def should_apply_d50_patch(icc_bytes):
    """Determine if D50 patch should be applied based on D50_PATCH_MODE setting.
    Returns True if patch should be applied, False otherwise."""
    mode = D50_PATCH_MODE.lower()

    if mode == "on":
        return True
    elif mode == "off":
        return False
    elif mode == "auto":
        return _d50_near_miss(icc_bytes)
    else:
        # Invalid mode, default to auto
        logger.warning(f"Invalid D50_PATCH_MODE '{D50_PATCH_MODE}', using 'auto'")
        return _d50_near_miss(icc_bytes)

def apply_d50_policy(icc_bytes, tiff_path):
    """Apply the D50 illuminant patch (if policy says so) and update stats.
    Accepts and returns raw ICC bytes. Safe to call with None (returns None)."""
    if icc_bytes is None:
        return None
    icc = bytearray(icc_bytes)

    if should_apply_d50_patch(bytes(icc)):
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
            elif _d50_near_miss(bytes(icc)):
                _d50_patch_count["skipped_needed"] += 1
            else:
                _d50_patch_count["other_illuminant"] += 1
        if was_correct:
            logger.debug(f"D50 patch skipped for {Path(tiff_path).name} (already correct)")
        elif _d50_near_miss(bytes(icc)):
            logger.debug(f"D50 patch skipped for {Path(tiff_path).name} (would have needed patch)")
        else:
            logger.debug(f"D50 patch skipped for {Path(tiff_path).name} (other illuminant, left as is)")

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


_djxl_icc_args = _common._djxl_icc_args


_decoded_in_original_space = _common._decoded_in_original_space


# ─── Explorer thumbnail (EXPLORER_THUMBNAIL) ─────────────────────────────
# One copy, in jxl_photo_common.py.
THUMB_BOX_UUID = _common.THUMB_BOX_UUID
THUMB_BOX_MARKER = _common.THUMB_BOX_MARKER
_THUMB_HEAD = _common._THUMB_HEAD


_jxl_boxes = _common._jxl_boxes


_codestream_fingerprint = _common._codestream_fingerprint


_thumbnail_box_fingerprint = _common._thumbnail_box_fingerprint


_write_thumbnail_box = _common._write_thumbnail_box


_write_metadata_stub = _common._write_metadata_stub


_assembled_size = _common._assembled_size


_assemble_from_stub = _common._assemble_from_stub


def _thumbnail_for_assembly(*a, **k): return _common._thumbnail_for_assembly(_G, *a, **k)


_JXL_THUMB_CLSID = "{EF830030-FBD6-4B62-BFD4-0DBD590D6135}"
_JXL_THUMB_HANDLER_KEYS = (
    r".jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}",
    r"SystemFileAssociations\.jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}",
)
_jxl_thumb_seen = []


def _jxl_thumb_installed(*a, **k): return _common._jxl_thumb_installed(_G, *a, **k)


def _explorer_thumbnail_wanted(*a, **k): return _common._explorer_thumbnail_wanted(_G, *a, **k)


_pam_raw_input = _common._pam_raw_input
_pam_array = _common._pam_array


def _make_explorer_thumbnail(*a, **k): return _common._make_explorer_thumbnail(_G, *a, **k)


def _add_explorer_thumbnail(*a, **k): return _common._add_explorer_thumbnail(_G, *a, **k)


def _cautious_test_icc_depth(icc_bytes: bytes, depth: int, distance=None) -> bool:
    """Run one round-trip test at the given bit depth. Returns True if safe.

    distance: the distance THIS file is encoded at (None = CJXL_DISTANCE). A
    film scan under SCAN_DISTANCE is tested at its own distance (#573)."""
    if distance is None:
        distance = CJXL_DISTANCE
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
            "-d", str(distance),
            "--effort", "1",
            "--container=1",
        ]
        if CJXL_MODULAR and distance > 0:
            cmd.append("--modular=1")

        r = _run_captured(cmd, 120)
        if r.returncode != 0:
            logger.debug(f"Cautious ICC test encode failed at {depth}-bit: {_stderr_tail(r.stderr)}")
            return False

        out_png = tmp / "out.png"
        icc_args, out_icc, orig_icc = _djxl_icc_args(tmp)
        r = _run_captured(["djxl", str(jxl_path), str(out_png)] + icc_args, 120)
        if r.returncode != 0 or not out_png.exists():
            logger.debug(f"Cautious ICC test decode failed at {depth}-bit: {_stderr_tail(r.stderr)}")
            return False

        if distance > 0 and _decoded_in_original_space(out_icc, orig_icc) is False:
            # A table-curve profile (ROMM toe, eciRGB v2, scanner LUTs) has no
            # native JXL form, so a LOSSY cjxl stores the whole ICC blob and
            # djxl returns linear sRGB: a decoder that pastes the original ICC
            # on those pixels gets wrong colours. The brightness check below
            # cannot see that — refuse "embed" outright.
            logger.debug(f"Cautious ICC test {depth}-bit: lossy ICC blob — the "
                         f"decode would come back in linear sRGB; unsafe to embed")
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


def _cautious_should_embed_icc(icc_bytes: bytes, tiff_path: Path, distance=None) -> bool:
    """Test an ICC profile with a small round-trip before trusting it.

    The lock covers the cache's read-modify-write, NOT the probe. The probe is
    two cjxl runs and two djxl runs with a 120 s timeout each, and holding the
    lock across them stopped every other worker dead the first time each
    profile was seen. Two threads racing on the same unseen profile now both
    probe it and reach the SAME verdict — the same benign race
    _content_id_cache already accepts — and the loser's store is a no-op.
    """
    if distance is None:
        distance = CJXL_DISTANCE
    key = hashlib.sha256(icc_bytes).hexdigest()
    # The key names the distance the file is encoded at, not the global one
    # (#573): a lossless verdict must never answer for a lossy scan.
    # Include the cjxl version in the key: an encoder upgrade can change
    # how a profile behaves, so stale verdicts from older cjxl builds
    # must not be trusted. ":t=2" is the test-format generation: t=2 added
    # the lossy-ICC-blob refusal, so a t=1 "embed" verdict for a table-curve
    # profile must be retested instead of trusted.
    cjxl_ver = _tool_version(_get_cjxl_cmd() or "cjxl") or (0, 0, 0)
    versioned_key = f"{key}:d={distance}:m={1 if CJXL_MODULAR else 0}:v={'.'.join(map(str, cjxl_ver))}:t=2"

    def _cached_verdict():
        cached = _load_icc_cache().get(versioned_key)
        if cached is None:
            return None
        return bool(cached.get("embed")) if isinstance(cached, dict) else bool(cached)

    with _icc_test_lock:
        embed = _cached_verdict()
    if embed is not None:
        logger.debug(f"Cautious ICC: cache hit for {tiff_path.name} -> {'embed' if embed else 'skip'}")
        return embed

    if not _get_cjxl_cmd() or not shutil.which("djxl"):
        logger.warning("cautious ICC strategy requires cjxl and djxl; falling back to heuristic")
        return _should_embed_icc_heuristic(icc_bytes)

    safe = True
    for depth in (8, 16):
        if not _cautious_test_icc_depth(icc_bytes, depth, distance=distance):
            safe = False
            break

    with _icc_test_lock:
        # Re-read inside the lock: another thread may have finished the same
        # probe while this one ran, and the cache file is shared with other
        # PROCESSES too, so the copy read before the probe is already stale.
        cache = _load_icc_cache()
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


def should_embed_icc_in_png(icc_bytes, lossy=True, tiff_path=None, distance=None):
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
        return _cautious_should_embed_icc(icc_bytes, tiff_path or Path("unknown"),
                                          distance=distance)

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

def _private_tiff_tags(tif, page):
    """The page's private tags to carry (TIFFTAG_XMP_PREFIX), as
    ([(code, type, raw bytes)], why_none_or_None).

    The bytes are read from the file as stored, not from tifffile's decoded
    value (an ASCII value comes back without its NUL and decoded). All or
    nothing: when one candidate exceeds PRIVATE_TAG_MAX_BYTES none of the
    program's tags is carried and `why` says which tag, so the caller can log
    it.

    The IPTC block (_IPTC_TAG) is the one exception, on every count: it is
    accepted as a LONG too (its token says UNDEFINED, see _IPTC_TAG_TYPES) and
    it is judged apart from the program's own tags, in both directions. An
    IIM over PRIVATE_TAG_MAX_BYTES is left out alone (see _iptc_left_out_note)
    and neither empties the list nor counts as "too large to carry" for the
    delete gate (#620): its fields are in the XMP. And when ANOTHER tag is too
    large (Affinity Photo's document), the list still holds the IPTC block,
    beside the `why` that keeps the TIFF from being deleted.

    The Photoshop image resources (_PHOTOSHOP_TAG) follow the same rule:
    carried with their own type when they fit, left out alone when they do
    not (_photoshop_left_out_note), never a veto.
    """
    found, iptc, why = [], [], None
    for tag in page.tags.values():
        code = int(tag.code)
        if code < 32768 or code in _PRIVATE_TAG_SKIP:
            continue
        try:
            dtype = int(tag.dtype)
        except (TypeError, ValueError):
            continue
        size = int(tag.valuebytecount)
        if code == _IPTC_TAG:
            if dtype in _IPTC_TAG_TYPES and size <= PRIVATE_TAG_MAX_BYTES:
                tif.filehandle.seek(tag.valueoffset)
                iptc.append((code, 7, tif.filehandle.read(size)))
            continue
        if code == _PHOTOSHOP_TAG:
            if dtype in _PRIVATE_TAG_TYPES and size <= PRIVATE_TAG_MAX_BYTES:
                tif.filehandle.seek(tag.valueoffset)
                iptc.append((code, dtype, tif.filehandle.read(size)))
            continue
        if dtype not in _PRIVATE_TAG_TYPES or why:
            continue
        if size > PRIVATE_TAG_MAX_BYTES:
            why = f"tag {code} is {size:,} bytes (limit {PRIVATE_TAG_MAX_BYTES:,})"
            continue
        tif.filehandle.seek(tag.valueoffset)
        found.append((code, dtype, tif.filehandle.read(size)))
    if why:
        return iptc, why
    return sorted(found + iptc), None


def _iptc_left_out_note(page):
    """Why the page's IPTC block is NOT carried as a token, or None.

    The block is bigger than PRIVATE_TAG_MAX_BYTES, or stored as a TIFF type
    that is not a byte stream here (_IPTC_TAG_TYPES): it stays out (the fields
    still reach the XMP through _fill_xmp_from_iptc) while the page's other
    private tags are carried as usual. Never silent: the caller logs it.
    """
    tag = page.tags.get(_IPTC_TAG)
    if tag is None:
        return None
    try:
        size = int(tag.valuebytecount)
        dtype = int(tag.dtype)
    except (TypeError, ValueError):
        return None
    if dtype not in _IPTC_TAG_TYPES:
        return f"IPTC block is stored as TIFF type {dtype}: kept as XMP fields only"
    if size > PRIVATE_TAG_MAX_BYTES:
        return (f"IPTC block is {size:,} bytes (limit {PRIVATE_TAG_MAX_BYTES:,}): "
                f"kept as XMP fields only")
    return None


def _irb_resources(data: bytes) -> list:
    """[(id, payload)] of a Photoshop image resource block ("8BIM" records,
    big-endian whatever the TIFF's byte order). Stops at the first record that
    does not parse: what was read so far is returned."""
    out, pos = [], 0
    while pos + 12 <= len(data) and data[pos:pos + 4] == b"8BIM":
        rid = struct.unpack(">H", data[pos + 4:pos + 6])[0]
        name_len = data[pos + 6]
        pos += 6 + 1 + name_len + ((name_len + 1) % 2)       # the name is padded to even
        if pos + 4 > len(data):
            break
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        if pos + 4 + size > len(data):
            break
        out.append((rid, data[pos + 4:pos + 4 + size]))
        pos += 4 + size + (size % 2)
    return out


def _photoshop_block(tif, page):
    """The page's Photoshop image resources as stored, or None (no tag, a type
    that is not a byte stream, or unreadable)."""
    tag = page.tags.get(_PHOTOSHOP_TAG)
    if tag is None:
        return None
    try:
        if int(tag.dtype) not in _PRIVATE_TAG_TYPES:
            return None
        tif.filehandle.seek(tag.valueoffset)
        return tif.filehandle.read(int(tag.valuebytecount))
    except Exception:
        return None


def _photoshop_left_out_note(tif, page):
    """(note, has_paths) for a page whose Photoshop image resources are NOT
    carried as a token (bigger than PRIVATE_TAG_MAX_BYTES), else (None, False).
    The note says what the block holds; has_paths is True when a saved path
    (a clipping path) is among it — the one item a user drew by hand, so the
    caller warns for it and only mentions the rest."""
    tag = page.tags.get(_PHOTOSHOP_TAG)
    if tag is None:
        return None, False
    try:
        size = int(tag.valuebytecount)
    except (TypeError, ValueError):
        return None, False
    if size <= PRIVATE_TAG_MAX_BYTES:
        return None, False
    ids = [rid for rid, _p in _irb_resources(_photoshop_block(tif, page) or b"")]
    has_paths = any(rid in _IRB_PATHS for rid in ids)
    what = []
    if any(rid in (0x0409, 0x040C) for rid in ids):
        what.append("a preview")
    if has_paths:
        what.append("saved paths")
    what.append("print and document settings")
    return (f"Photoshop resource block is {size:,} bytes (limit {PRIVATE_TAG_MAX_BYTES:,}): "
            f"not kept ({', '.join(what)})", has_paths)


def _iptc_stream(tif):
    """Page 0's IPTC (IIM) stream, byte for byte: the IPTC tag when the TIFF
    has one, else the copy inside the Photoshop image resources (Sony's Edit
    and other programs write it only there). None when there is none."""
    try:
        page = tif.pages[0]
        tag = page.tags.get(_IPTC_TAG)
        if tag is not None:
            tif.filehandle.seek(tag.valueoffset)
            return tif.filehandle.read(int(tag.valuebytecount))
        for rid, payload in _irb_resources(_photoshop_block(tif, page) or b""):
            if rid == _IRB_IPTC and payload:
                return payload
    except Exception:
        return None
    return None


def _has_iptc(tif) -> bool:
    """True when the TIFF's first page has an IPTC block, in its own tag or
    inside the Photoshop image resources: the only fact that decides whether
    _fill_xmp_from_iptc runs (no exiftool call to find out)."""
    try:
        if _IPTC_TAG in tif.pages[0].tags:
            return True
    except Exception:
        return False
    return bool(_iptc_stream(tif))


def _iptc_only_source(tiff_path, tmp_dir) -> str:
    """A 1x1 TIFF in tmp_dir whose only content is page 0's IPTC block, byte
    for byte (any size: the token's limit does not apply here). Raises when
    the tag cannot be read: an output must not go out without the fields."""
    with tifffile.TiffFile(str(tiff_path)) as tif:
        raw = _iptc_stream(tif)
        if raw is None:
            raise RuntimeError("the IPTC block disappeared from the TIFF while encoding")
    out = os.path.join(str(tmp_dir), "iptc_source.tif")
    tifffile.imwrite(out, np.zeros((1, 1), dtype=np.uint8),
                     extratags=[(_IPTC_TAG, 7, len(raw), raw, True)])
    return out


def _fill_xmp_from_iptc(tiff_path, write_path, tmp_dir) -> list:
    """The exiftool command that copies the TIFF's IPTC fields into the
    master's XMP — only the ones the XMP does not hold yet
    (IPTC_TO_XMP_FILL); convert_one runs it with the other writes.

    ONE exiftool command, a command of its own: `-wm cg` (create tags and groups,
    never overwrite one that exists; measured 2026-11-11 on single fields and
    on lists such as dc:Subject) is global to the call and would break the
    `=` assignments of the others. The XMP the master already carries always
    wins; nothing goes into the EXIF (the IPTC has a date, not a time, and an
    invented hour would be a guess). The charset is the IIM's own
    (CodedCharacterSet); an IIM without one is read as exiftool reads it,
    never "fixed" here. A failed command raises: the file is an error and
    --delete-source keeps the TIFF (#571).

    A source whose name has a '%' cannot be the -tagsFromFile operand as it is
    (exiftool format-expands it, #550), and _exiftool_source's MIE dump is no
    way out here: the MIE keeps the IPTC in Latin and every Japanese caption
    or keyword arrives as '?' (measured 2026-11-11). Such a source is handed
    over as a small TIFF in tmp_dir that holds only its IPTC block
    (_iptc_only_source): the bytes are the ones exiftool reads from the file.
    """
    source = str(tiff_path)
    if "%" in source:
        source = _iptc_only_source(tiff_path, tmp_dir)
    args = ["-overwrite_original", "-wm", "cg", "-tagsfromfile", source]
    args += [f"-{xmp}<IPTC:{iptc}" for iptc, xmp in IPTC_TO_XMP_FILL]
    args.append(str(write_path))
    return args


_collapse_list_reset = _common._collapse_list_reset


def _xmp_block_preload_args(xmp_file, write_path):
    """The exiftool command that writes the source's XMP into write_path as
    one BLOCK, before the metadata command edits it; None when there is no
    XMP. convert_one runs it first among the writes.

    A field-by-field copy (-tagsfromfile -xmp:all) carries only what exiftool
    can WRITE, and drops a namespace it can merely read: SilverFast keeps its
    IR/iSRD settings in one (`LSI/`), and a scan decoded without it is no
    longer recognised as having an IR channel. The block keeps every byte.
    It must be a command of its own: in the same command as the encode record
    and marker edits, exiftool writes the block and IGNORES those edits; and
    the edit command that follows must not copy -xmp:all again, or every list
    it appends to (+=) is written twice (#531). The recompressor and the
    decoder copy the block the same way (-tagsfromfile <jxl> -XMP).
    """
    if not xmp_file:
        return None
    return ["-overwrite_original", "-api", "Compress=0", f"-XMP<={xmp_file}", str(write_path)]


def extract_xmp_original(tiff_path, tmp_dir):
    """Extract original XMP from TIFF as separate file for preservation.
    Returns path to XMP file or None if no XMP exists."""
    # Never the source's name: -XMP<= expands a '%' in it (#550).
    xmp_path = tmp_dir / "src_original.xmp"
    # Use an argfile (like the other exiftool calls here): paths containing
    # [ ] would be treated as wildcards on the command line, and non-ASCII
    # paths need the UTF-8 charset directive.
    # Correct order: -o output.xmp -b -XMP input.tif
    arg_file = tmp_dir / "xmp_extract.args"
    arg_file.write_text(f"{_ARGFILE_CHARSET}-o\n{xmp_path}\n-b\n-XMP\n{tiff_path}\n", encoding="utf-8")
    _run_captured(
        [_get_exiftool_cmd(), "-@", str(arg_file)],
        EXIFTOOL_TIMEOUT
    )
    if xmp_path.exists() and xmp_path.stat().st_size > 0:
        return xmp_path
    return None

# ---------------------------------------------------------------------------
# Encoding-parameter record ("gen=N | cjxl d=X e=Y | ..." machine block)
#
# Append-only lineage: every encode/re-encode APPENDS one cjxl entry to the
# chain and reconciles the gen= token at its head — derived from the chain,
# never incremented, so a wrong value self-corrects on the next pass. The
# user's own text (a caption) always stays first: dc:Description is visible
# in Windows Properties. These helpers are deliberately identical to the
# copies in jxl_recompressor.py (pinned by tests/test_helper_parity.py).
# ---------------------------------------------------------------------------

# The encoding-parameters tag: "cjxl d=0.1 e=7", possibly several in a
# " | "-separated chain (the LAST one is the current file's).
_ENCODE_TAG_RE = _common._ENCODE_TAG_RE

_GEN_TAG_RE = _common._GEN_TAG_RE

_MACHINE_BLOCK_RE = _common._MACHINE_BLOCK_RE

# Divergence between a stored gen= and the chain length is logged once per
# run, not per file. Reset in main().
_gen_divergence_logged = False


_NO_RECORD_SEGMENT = _common._NO_RECORD_SEGMENT


_history_record = _common._history_record


_strip_encode_params = _common._strip_encode_params


_reconcile_gen = _common._reconcile_gen


_append_encode_entry = _common._append_encode_entry


_merge_lineage_blocks = _common._merge_lineage_blocks


def _log_gen_notes_once(*a, **k): return _common._log_gen_notes_once(_G, *a, **k)


def _encode_tag_mode(*a, **k): return _common._encode_tag_mode(_G, *a, **k)


def _visible_record_args(*a, **k): return _common._visible_record_args(_G, *a, **k)


def _record_fields_read_args(tiff_path):
    """The exiftool command _read_record_fields runs."""
    return ["-j", "-Software", "-XMP-exif:UserComment", "-XMP-xmpMM:HistoryParameters",
            str(tiff_path)]


def _description_read_args(xmp_path):
    """The exiftool command read_existing_description runs."""
    return ["-s3", "-XMP-dc:Description", str(xmp_path)]


def _relation_read_args(xmp_path):
    """The exiftool command read_existing_relation runs."""
    return ["-j", "-XMP-dc:Relation", str(xmp_path)]


def _creator_tool_read_args(xmp_path):
    """The exiftool command read_existing_creator_tool runs."""
    return ["-s", "-XMP-xmp:CreatorTool", str(xmp_path)]


def _read_record_fields(tiff_path, tmp_dir):
    """(Software, XMP exif:UserComment, xmpMM:History record) of a source TIFF,
    in one exiftool call. Raises when exiftool fails: an unread field cannot
    tell a stale record from none, and -tagsfromfile would carry the stale one
    into the JXL — fail CLOSED on the file, as the Software read always did.
    Empty output with rc=0 is the normal state of a TIFF with none of them."""
    r = _run_exiftool_argfile(_record_fields_read_args(tiff_path), timeout=EXIFTOOL_TIMEOUT)
    if r.returncode != 0:
        _err = (r.stderr or r.stdout or "no output")[:200]
        raise RuntimeError(f"exiftool could not read Software from {tiff_path.name} "
                           f"(rc={r.returncode}): {_err.strip()}")
    try:
        data = json.loads(r.stdout) if (r.stdout or "").strip() else []
    except ValueError:
        raise RuntimeError(f"exiftool could not read Software from {tiff_path.name}: "
                           f"output is not JSON ({(r.stdout or '')[:80]!r})")
    entry = data[0] if data else {}
    return (str(entry.get("Software") or "").strip(),
            str(entry.get("UserComment") or "").strip(),
            _history_record(entry.get("HistoryParameters")))


def read_existing_description(xmp_path):
    """Read existing dc:description from XMP file if present.
    Returns empty string if not found."""
    if not xmp_path or not xmp_path.exists():
        return ""
    try:
        # -s3 (NOT -s): only -s3 prints the bare VALUE. Plain -s prints
        # "Description                     : value", and returning that line
        # verbatim seeded the tag name into the new dc:Description (round 43).
        r = _run_exiftool_argfile(_description_read_args(xmp_path),
                                  timeout=EXIFTOOL_TIMEOUT)
        if r.returncode == 0 and r.stdout:
            stdout = r.stdout.strip()
            # The value is used UNFILTERED: exiftool's own warnings go to
            # STDERR (a failed read is refused by the returncode check), so a
            # stdout prefix filter ("Warning:", "[minor]", "[major]") can only
            # ever drop a real caption that begins with those words. Nor is it
            # split at a colon: a caption "Captured: autumn 1958" is a value.
            if r.stderr:
                logger.debug(f"Reading description: exiftool stderr: "
                             f"{r.stderr.strip()[:200]}")
            return stdout
    except Exception as e:
        logger.debug(f"Failed to read description: {e}")
    return ""

_INTERNAL_RELATION_PREFIXES = (
    "jxlphoto-src:",
    "jxlphoto-srcsum:",
    "jxlphoto-mpg:",
    "jxlphoto-subfiletype:",
    "jxlphoto-depth:",
    "jxlphoto-page:",
    "jxlphoto-pages:",
    "jxlphoto-icc:inherited",
    "jxlphoto-grayscale",
    "jxlphoto-thumb",
    EXTRASAMPLES_XMP_PREFIX,
    # Re-read from the TIFF itself on every encode (_private_tiff_tags,
    # _scan_kind).
    TIFFTAG_XMP_PREFIX,
    SCAN_XMP_PREFIX,
    # The decoder's record of the pixels it wrote into a TIFF (its
    # PIXSUM_PREFIX): it describes THAT TIFF, never a master encoded from it.
    "jxlphoto-pixsum:",
    DERIVED_XMP_PREFIX,
    ORIGIN_PREFIX,
    # The ICC this encode writes fresh from the TIFF's own profile: a copy
    # carried in from the source (a TIFF decoded with --no-icc-cleanup) would
    # be a second, possibly stale, profile.
    ICC_PROFILE_PREFIX,
)


def read_existing_relation(xmp_path):
    """Read user dc:Relation items from an XMP file via exiftool JSON output,
    filtering out this tool's own internal round-trip markers (jxlphoto-*).
    Returns a list of strings (may be empty)."""
    if not xmp_path or not Path(xmp_path).exists():
        return []
    try:
        r = _run_exiftool_argfile(_relation_read_args(xmp_path), timeout=EXIFTOOL_TIMEOUT)
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


_icc_profile_token = _common._icc_profile_token


def read_existing_creator_tool(xmp_path):
    """Read existing CreatorTool from XMP file if present.
    Returns empty string if not found."""
    if not xmp_path or not xmp_path.exists():
        return ""
    try:
        r = _run_exiftool_argfile(_creator_tool_read_args(xmp_path),
                                  timeout=EXIFTOOL_TIMEOUT)
        if r.returncode == 0 and r.stdout:
            stdout = r.stdout.strip()
            if " : " in stdout:
                return stdout.split(" : ", 1)[1].strip()
            elif ":" in stdout:
                return stdout.split(":", 1)[1].strip()
    except Exception as e:
        logger.debug(f"Failed to read CreatorTool: {e}")
    return ""

def build_metadata_injection_args(tiff_path, write_path, tmp_dir, exif_bin, icc_bytes, xmp_original, original_depth=16, strip_metadata=False, src_path_id=None, src_content_id=None, xmp_preloaded=False, distance=None):
    """Build exiftool arguments for metadata injection with proper XMP preservation.

    Strategy:
    1. Inject EXIF binary if available
    2. Copy all metadata from source TIFF (preserving original XMP)
    3. Add/modify specific XMP tags without overwriting the whole package

    Args:
        strip_metadata: If True, strip all metadata (no EXIF/XMP preservation)
        xmp_preloaded: the source's XMP is written into write_path as a BLOCK
            (_xmp_block_preload_args) by the command that runs just before
            this one, in the same batch, so this command must not copy
            -xmp:all again — see _xmp_block_preload_args. Nothing here reads
            write_path.
        distance: the distance this file was encoded at, for the encode record
            (None = CJXL_DISTANCE; a film scan can differ, see SCAN_DISTANCE).

    Returns path to arg file.
    """
    args_lines = ["-overwrite_original"]
    _rec_d = CJXL_DISTANCE if distance is None else distance

    # If stripping metadata, only add minimal encoding info and exit
    if strip_metadata:
        # Strip all EXIF first
        args_lines.append("-exif:all=")
        # Strip all XMP (must come BEFORE setting new Description)
        args_lines.append("-xmp:all=")
        # Then the encode record — the only metadata --strip keeps: its History
        # entry, plus the visible copy of the mode ("off": a History entry that
        # says there is none). Fresh file, fresh chain: gen= reconciles to the
        # lossy-entry count (1 lossy, 0 lossless).
        record, _s, _c, _o = _append_encode_entry("", _rec_d, CJXL_EFFORT)
        args_lines += _visible_record_args(_encode_tag_mode(), record, "", "", "",
                                           tiff_path.name)
        args_lines += _history_args(
            "converted", tiff_path, write_path,
            record=_NO_RECORD_SEGMENT if _encode_tag_mode() == "off" else record,
            codec=_get_cjxl_cmd() or "cjxl")
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
    args_lines.append(_exiftool_source(tiff_path, tmp_dir))      # '%' names: #550
    args_lines.append("-exif:all")
    if not xmp_preloaded:
        args_lines.append("-xmp:all")

    # The reads below (the source's record fields and origin, the extracted
    # XMP's Relation, Description and CreatorTool) in ONE exiftool process:
    # each reader's own call then finds its answer kept for it.
    _reads = [_record_fields_read_args(tiff_path), _origin_read_args(tiff_path)]
    if xmp_original:
        _reads += [_relation_read_args(xmp_original), _description_read_args(xmp_original),
                   _creator_tool_read_args(xmp_original)]
    _prefetch_exiftool_reads(_reads, EXIFTOOL_TIMEOUT)

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
    
    # 3. The encode record (gen=N | cjxl d=X e=Y | ...), APPEND-ONLY: every
    # encode appends one "cjxl d= e=" entry and reconciles gen= (a re-encode
    # at identical d/e appends too: decode-then-re-encode is exactly where a
    # generation of loss happens, and deduplicating it would undercount what
    # the recompressor's --on-regeneration guards). Since v2.24.0 its home is
    # this write's xmpMM:History entry (_history_args(record=...) below);
    # ENCODE_TAG_MODE only decides where a VISIBLE copy goes. The source can
    # carry the record in the History (a decode of a v2.24.0+ master), in
    # dc:Description or Software (older masters, --encode-tag title/software):
    # the union is the chain, and every visible field that held a record is
    # cleaned (user text, e.g. a caption, kept) so no stale copy survives.
    existing_desc = read_existing_description(xmp_original) if xmp_original else ""
    original_sw, original_comment, existing_history = _read_record_fields(
        tiff_path, tmp_dir)
    merged_entries, merged_stored = _merge_lineage_blocks(
        existing_desc, original_sw, existing_history)
    merged_chain = " | ".join(
        ([f"gen={merged_stored}"] if merged_stored else [])
        + [f"cjxl d={d} e={e}" for d, e in merged_entries])
    record, stored, counted, _orphans = _append_encode_entry(
        merged_chain, _rec_d, CJXL_EFFORT)
    _log_gen_notes_once(stored, counted)
    history_record = _NO_RECORD_SEGMENT if _encode_tag_mode() == "off" else record
    args_lines += _visible_record_args(_encode_tag_mode(), record, existing_desc,
                                       original_sw, original_comment, tiff_path.name)

    # Always store original bit depth in dc:Relation so the decoder can restore
    # the original BitsPerSample per page according to --depth-policy.
    args_lines.append(f"-XMP-dc:Relation+={DEPTH_XMP_PREFIX}{original_depth}")

    # WHERE the first master of this photo came from (ORIGIN_PREFIX): carried
    # forward when the TIFF already names it (a decode of one of our masters),
    # this TIFF's own depth when the TIFF is clean, nothing when the toolkit
    # touched it before without recording one.
    _o_tokens, _o_record = _read_origin_inputs(tiff_path)
    _origin, _inh = _origin_for_output(_o_tokens, _o_record, f"tiff{original_depth}")
    if _origin:
        args_lines.append(f"-XMP-dc:Relation+={ORIGIN_PREFIX}{_argfile_safe(_origin)}")

    # Which toolkit version wrote this master, and its encode record.
    args_lines += _history_args("converted", tiff_path, write_path, record=history_record,
                                codec=_get_cjxl_cmd() or "cjxl")

    # WHICH source made this output. Written for every output, not just for
    # multi-page splits: it is what lets a later run tell "the same photo,
    # re-exported" from "a different photo that happens to share this name"
    # once the mode has collapsed the folder structure away.
    if src_path_id:
        args_lines.append(f"-XMP-dc:Relation+={SRC_PREFIX}{src_path_id}")
    if src_content_id:
        args_lines.append(f"-XMP-dc:Relation+={SRCSUM_PREFIX}{src_content_id}")
    
    # 4. The original ICC, byte for byte, as a jxlphoto-iccprofile token in
    # dc:Relation (round-trip preservation; independent of ENCODE_TAG_MODE).
    # After the Relation reset above, so it is the only one the master holds.
    if EMBED_ICC_IN_JXL and icc_bytes:
        args_lines.append("-XMP-dc:Relation+=" + _icc_profile_token(icc_bytes))

    # 4b. CreatorTool names the program that made the photo, nothing else.
    # Up to v2.18.x the ICC went there as "ICC:<base64>"; a TIFF decoded with
    # --no-icc-cleanup (or re-tagged by hand) still carries it, and the XMP copy
    # above would bring it into the master: strip it, keeping the real text
    # ("Capture One Windows"). An empty value removes the tag.
    if xmp_original:
        existing_creator = read_existing_creator_tool(xmp_original)
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
            args_lines.append(f"-xmp-xmp:CreatorTool={_argfile_safe(existing_creator)}")

    _clear_exiftool_prefetch()

    # 5. Cleanup legacy ICC markers from XMP if requested
    if CLEANUP_XMP_ICC_MARKER:
        # Remove common legacy ICC marker tags that might conflict
        args_lines.append("-xmp-icc:all=")  # Clear any XMP ICC tags if present
        args_lines.append("-xmp-photoshop:ICCProfile=")  # Clear Photoshop ICC refs if any
    
    # 6. The EXIF keeps the TIFF's own byte order (II or MM). Until v2.24.x
    #    every master was forced to little-endian. Set explicitly because
    #    exiftool writes a NEW EXIF block (no raw block extracted) big-endian.
    args_lines.append(f"-ExifByteOrder={_tiff_byte_order(tiff_path)}")
    
    # 7. Target file
    args_lines.append(str(write_path))
    # The XMP is in the file already when it was preloaded as a block:
    # replace dc:Relation with all-`=` lines (see _collapse_list_reset).
    if xmp_preloaded:
        args_lines = _collapse_list_reset(args_lines)

    # Write args file (UTF-8 charset first so non-ASCII paths work)
    arg_file = tmp_dir / "inject.args"
    arg_file.write_text(_ARGFILE_CHARSET + "\n".join(args_lines) + "\n", encoding="utf-8")
    return arg_file

# ═══════════════════════════════════════════════════════════════════════════════

# Rows filtered and compressed at a time by make_png_bytes (#569).
_PNG_BLOCK_ROWS = 256


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
    bit_depth = 8 if img.dtype == np.uint8 else 16

    # Streamed (#569): rows go through ONE zlib stream block by block, so
    # neither the big-endian copy of the image nor the raw filtered buffer
    # ever exists whole, and the file is assembled with a single join. The
    # bytes are identical to zlib.compress(raw, 0) over the whole buffer —
    # 45 MP used to cost ~1-1.4 GB of transient Python memory per worker.
    # One compress() call per block, the filter byte (0) and the big-endian
    # samples laid out by numpy: a Python loop per row held the GIL and made
    # four parallel workers 2.5x slower than one (measured on 45 MP).
    # Level 0 (stored), not 1: the PNG only carries the pixels to cjxl, and
    # 16-bit photo data barely compresses (5.93 vs 6.00 bytes per pixel on a
    # 43 MP Z8 export) while level 1 cost 6.55 s of Python per file against
    # 0.40 s; peak memory 11.97 vs 12.12 B/px, inside _PYTHON_BYTES_PER_PIXEL
    # (a page of 4 channels peaks higher: _python_bytes_per_pixel)
    # (refactor B2, measured 2026-11-13).
    comp = zlib.compressobj(0)
    idat = []
    row_bytes = w * c * (2 if bit_depth == 16 else 1)
    for y in range(0, h, _PNG_BLOCK_ROWS):
        block = img[y:y + _PNG_BLOCK_ROWS]
        n = block.shape[0]
        filtered = np.empty((n, row_bytes + 1), dtype=np.uint8)
        filtered[:, 0] = 0
        samples = block.astype(">u2") if bit_depth == 16 else block
        filtered[:, 1:] = np.ascontiguousarray(samples).reshape(n, -1).view(np.uint8)
        piece = comp.compress(filtered)
        if piece:
            idat.append(piece)
    idat.append(comp.flush())
    idat_len = sum(len(p) for p in idat)
    idat_crc = zlib.crc32(b"IDAT")
    for piece in idat:
        idat_crc = zlib.crc32(piece, idat_crc)

    parts = [b"\x89PNG\r\n\x1a\n",
             chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bit_depth, color_type, 0, 0, 0))]
    if icc_bytes:
        parts.append(chunk(b"iCCP", b"ICC Profile\x00\x00" + zlib.compress(icc_bytes)))
    parts.append(struct.pack(">I", idat_len) + b"IDAT")
    parts.extend(idat)
    parts.append(struct.pack(">I", idat_crc & 0xFFFFFFFF))
    parts.append(chunk(b"IEND", b""))
    return b"".join(parts)

_exif_box_rank = _common._exif_box_rank


reorder_jxl_boxes = _common.reorder_jxl_boxes

_magick_out_path = _common._magick_out_path


def _is_thumbnail_page(page) -> bool:
    """Return True if the TIFF page is a thumbnail or pyramid level.

    Uses standard TIFF SubfileType flags exposed by tifffile:
    - is_reduced  : reduced-resolution image (thumbnail / pyramid)
    - is_subifd   : SubIFD of another page (pyramid / preview)
    """
    return bool(page.is_reduced or page.is_subifd)

def _page_facts(i: int, page) -> dict:
    """One page's entry in _analyze_tiff_pages' page_info. Header fields only
    (no pixel decode). Page 0 also gets 'software' (its Software tag, '' when
    absent), 'icc_class' (the ICC profile's device class, e.g. 'scnr' for a
    scanner profile, 'mntr' for sRGB/Adobe RGB; None without a profile),
    'bits' and 'camera' (the Exif IFD records an exposure, an aperture or a
    focal length: a camera wrote it, a scanner never does) — what _scan_kind
    reads. Every page gets 'size' (width, height)."""
    facts = {
        'subfiletype': int(page.subfiletype) if page.subfiletype else 0,
        'samples': int(page.samplesperpixel) if page.samplesperpixel else 1,
    }
    try:
        facts['size'] = (int(page.imagewidth or 0), int(page.imagelength or 0))
    except (TypeError, ValueError):
        facts['size'] = (0, 0)
    try:
        facts['photometric'] = int(page.photometric)
    except (TypeError, ValueError):
        pass
    try:
        facts['extrasamples'] = tuple(int(v) for v in (page.extrasamples or ()))
    except (TypeError, ValueError):
        facts['extrasamples'] = ()
    if i == 0:
        try:
            _sw = page.tags.get(305)
            facts['software'] = str(_sw.value) if _sw is not None else ""
        except Exception:
            facts['software'] = ""
        try:
            _icc = page.tags.get(34675)
            _icc = bytes(_icc.value) if _icc is not None else b""
            facts['icc_class'] = (_icc[12:16].decode("latin-1").strip()
                                  if len(_icc) >= 16 else None)
        except Exception:
            facts['icc_class'] = None
        try:
            _bits = page.bitspersample
            facts['bits'] = int(max(_bits) if isinstance(_bits, (tuple, list)) else _bits)
        except (TypeError, ValueError):
            facts['bits'] = 0
        try:
            _exif = page.tags.get(34665)
            _exif = _exif.value if _exif is not None else {}
            facts['camera'] = isinstance(_exif, dict) and any(
                k in _exif for k in _CAMERA_EXIF_KEYS)
        except Exception:
            facts['camera'] = False
    return facts


# Exif IFD fields only a camera writes; their absence (with no Software tag)
# is what marks a lab scan or an Epson Scan TIFF.
_CAMERA_EXIF_KEYS = ("ExposureTime", "FNumber", "FocalLength", "ISOSpeedRatings",
                     "PhotographicSensitivity")


def _has_ir(page_info: dict) -> bool:
    """An IR page (SubfileType bit 2, mask) or a 2nd/4th channel that is not
    alpha (ExtraSamples unspecified or absent) on any page — pure."""
    for v in (page_info or {}).values():
        v = v or {}
        if int(v.get('subfiletype') or 0) & 4:
            return True
        if int(v.get('samples') or 0) in (2, 4) and not (
                set(v.get('extrasamples') or ()) & {1, 2}):
            return True
    return False


# The thumbnail size of the camera file standard (DCF), landscape or portrait.
_DCF_THUMBNAIL_SIZES = frozenset({(160, 120), (120, 160)})


def _scan_reason(page_info: dict):
    """Why this TIFF is (or is not) a film scan — pure.

    "software": its Software tag names a program in SCANNER_SOFTWARE.
    "ir":       an IR channel (_has_ir).
    "guessed":  neither a Software tag nor camera EXIF. Measured on a library
                of 15 000 TIFFs, the ~4 000 without a Software tag were lab
                scans, Epson Scan output and scanned documents — and 455
                camera TIFFs (Fuji S5 Pro, Nikon E990) rewritten without any
                tag, which the next case catches.
    "camera":   the same, but a later page is 160x120: the thumbnail a camera
                writes into its own TIFF. None of the ~2 300 scans had one.
                Not a scan; the encoder marks it so (jxlphoto-scan:no) for the
                recompressor, which cannot see the pages again.
    None:       any other TIFF.
    """
    info = page_info or {}
    p0 = info.get(0) or {}
    software = str(p0.get('software') or "").casefold()
    if any(str(n).casefold() in software for n in SCANNER_SOFTWARE if str(n).strip()):
        return "software"
    if _has_ir(info):
        return "ir"
    if software.strip() or p0.get('camera'):
        return None
    if any(tuple((v or {}).get('size') or ()) in _DCF_THUMBNAIL_SIZES
           for i, v in info.items() if i != 0):
        return "camera"
    return "guessed"


def _scan_kind(page_info: dict):
    """None for a TIFF that is not a film scan, else "raw" or "final" — pure.

    A scan: _scan_reason says "software", "ir" or "guessed". Raw: the
    scanner's own ICC profile (class 'scnr'), no profile at 16 bits, or an IR
    channel; otherwise final (converted to an output profile). The doubtful
    cases fall on the scan/raw side, where a mistake costs space, not quality.
    """
    info = page_info or {}
    p0 = info.get(0) or {}
    reason = _scan_reason(info)
    if reason not in ("software", "ir", "guessed"):
        return None
    icc_class = p0.get('icc_class')
    if reason == "ir" or _has_ir(info) or icc_class == 'scnr' or (
            not icc_class and int(p0.get('bits') or 0) >= 16):
        return "raw"
    return "final"


# normcase(tiff path) -> (scan kind or None, distance) decided at planning
# (_record_scan); read by convert_one and the delete gates through
# _distance_for, so the encode and its verification always agree.
_SCAN_PLAN = {}
# normcase(tiff path) -> _scan_reason, for the run's notices and the
# jxlphoto-scan:no marker.
_SCAN_REASON = {}
_SCAN_PLAN_LOCK = threading.Lock()


def _scan_distance_applies(kind) -> bool:
    return (SCAN_DISTANCE is not None and kind is not None
            and (str(SCAN_SCOPE).lower() == "all" or kind == "raw"))


def _parse_eight_bit_distance(value):
    """EIGHT_BIT_DISTANCE normalised: "auto", "same" or a float in 0..25.
    ValueError for anything else (None reads as "same")."""
    if value is None:
        return "same"
    t = str(value).strip().lower()
    if t in ("auto", "same"):
        return t
    d = float(t)
    if not 0 <= d <= 25:
        raise ValueError(value)
    return d


def _eight_bit_distance() -> float:
    """The distance this run gives an 8-bit TIFF that is not a film scan."""
    try:
        v = _parse_eight_bit_distance(EIGHT_BIT_DISTANCE)
    except (TypeError, ValueError):
        return CJXL_DISTANCE          # main() refuses a bad value before any encode
    if v == "same":
        return CJXL_DISTANCE
    if v == "auto":
        return 0.0 if CJXL_DISTANCE <= float(EIGHT_BIT_LOSSLESS_UP_TO) else CJXL_DISTANCE
    return float(v)


# normcase(tiff path) of the 8-bit TIFFs this run encodes at
# _eight_bit_distance() instead of CJXL_DISTANCE (for the notices and hints).
_EIGHT_BIT_PLAN = set()


def _record_scan(tiff_path, kind, reason=None, bits=0) -> None:
    """Decide, at planning time, the distance `tiff_path` is encoded at:
    SCAN_DISTANCE for a film scan it covers, _eight_bit_distance() for an
    8-bit TIFF that is not a scan (`bits`: page 0's BitsPerSample), the run's
    distance otherwise."""
    key = os.path.normcase(str(tiff_path))
    eight = False
    if _scan_distance_applies(kind):
        d = SCAN_DISTANCE
    elif kind is None and 0 < int(bits or 0) <= 8:
        d = _eight_bit_distance()
        eight = float(d) != float(CJXL_DISTANCE)
    else:
        d = CJXL_DISTANCE
    with _SCAN_PLAN_LOCK:
        _SCAN_PLAN[key] = (kind, float(d))
        _SCAN_REASON[key] = reason
        if eight:
            _EIGHT_BIT_PLAN.add(key)
        else:
            _EIGHT_BIT_PLAN.discard(key)


def _distance_for(tiff_path) -> float:
    """The distance this run encodes `tiff_path` at: SCAN_DISTANCE for a film
    scan it covers, CJXL_DISTANCE otherwise (and for anything not planned)."""
    with _SCAN_PLAN_LOCK:
        plan = _SCAN_PLAN.get(os.path.normcase(str(tiff_path)))
    return plan[1] if plan else CJXL_DISTANCE


def _scan_distance_hint(tiff_path) -> str:
    """'' — or, for a film scan this run judges at SCAN_DISTANCE instead of
    the run's --distance, the flag that matters: a scan archived at the run's
    distance (before v2.17.0, or with --scan-distance same) fails a
    comparison made at the scan distance, and "re-run with the archive's
    --distance" alone would not change that."""
    d = _distance_for(tiff_path)
    if d == CJXL_DISTANCE:
        return ""
    with _SCAN_PLAN_LOCK:
        eight = os.path.normcase(str(tiff_path)) in _EIGHT_BIT_PLAN
    if eight:
        return (f" (an 8-bit TIFF: this run judges it at EIGHT_BIT_DISTANCE {d:g}"
                f"{' (lossless)' if d == 0 else ''}, not --distance {CJXL_DISTANCE:g}; "
                f"if it was archived at the run's distance, add --eight-bit-distance same)")
    return (f" (a film scan: this run judges it at SCAN_DISTANCE {d:g}, not "
            f"--distance {CJXL_DISTANCE:g}; if it was archived at the run's distance, "
            f"add --scan-distance same)")


def _scan_kind_for(tiff_path):
    with _SCAN_PLAN_LOCK:
        plan = _SCAN_PLAN.get(os.path.normcase(str(tiff_path)))
    return plan[0] if plan else None


def _scan_reason_for(tiff_path):
    with _SCAN_PLAN_LOCK:
        return _SCAN_REASON.get(os.path.normcase(str(tiff_path)))


_SCAN_REASON_LABELS = (("software", "by scanner software (SCANNER_SOFTWARE)"),
                       ("ir", "with an IR page"),
                       ("guessed", "GUESSED (no Software tag, no camera EXIF)"))


def _scans_at_scan_distance(tiff_paths) -> dict:
    """reason -> [tiff paths] for the TIFFs among `tiff_paths` this run encodes
    at SCAN_DISTANCE instead of CJXL_DISTANCE (empty when the two agree)."""
    out = {}
    if SCAN_DISTANCE is None or SCAN_DISTANCE == CJXL_DISTANCE:
        return out
    with _SCAN_PLAN_LOCK:
        for t in tiff_paths:
            key = os.path.normcase(str(t))
            plan = _SCAN_PLAN.get(key)
            if plan and _scan_distance_applies(plan[0]):
                out.setdefault(_SCAN_REASON.get(key) or "guessed", []).append(Path(t))
    return out


def _log_scans_at_scan_distance(by_reason: dict, done: bool) -> None:
    """The film-scan notice: before the first encode (done=False, the TIFFs
    this run will write — an up-to-date scan in a sync run is not news) and in
    the summary (done=True, the TIFFs it wrote). A guessed scan is a WARNING
    naming its folders: a TIFF whose tags were lost lands there too, and it
    is encoded at another distance than the user asked for."""
    total = sum(len(v) for v in by_reason.values())
    if not total:
        return
    _d = f"{SCAN_DISTANCE:g}{' (lossless)' if SCAN_DISTANCE == 0 else ''}"
    _why = ", ".join(f"{len(by_reason[k])} {label}"
                     for k, label in _SCAN_REASON_LABELS if by_reason.get(k))
    logger.info(f"Film scans: {total} TIFF(s) {'encoded' if done else 'to encode'} at "
                f"distance {_d} instead of {CJXL_DISTANCE:g} (SCAN_DISTANCE) — {_why}")
    guessed = by_reason.get("guessed") or []
    if not guessed:
        return
    folders = {}
    for t in guessed:
        folders[str(t.parent)] = folders.get(str(t.parent), 0) + 1
    folders = sorted(folders.items(), key=lambda kv: (-kv[1], kv[0]))
    logger.warning(f"Film scans: {len(guessed)} of them are only a guess — no Software tag "
                   f"and no camera EXIF, like a lab or Epson scan, but also like a photo "
                   f"whose tags were lost:")
    for folder, n in folders[:5]:
        logger.warning(f"  {n:>5} | {folder}")
    if len(folders) > 5:
        logger.warning(f"  ... and {len(folders) - 5} more folder(s)")
    if done:
        logger.warning("  Not film scans? Re-run those folders with --scan-distance same "
                       "--overwrite.")
    else:
        logger.warning("  Not film scans? Stop now (Ctrl+C) and run those folders with "
                       "--scan-distance same.")


def _eight_bit_at_other_distance(tiff_paths) -> list:
    """The 8-bit TIFFs among `tiff_paths` this run encodes at
    _eight_bit_distance() instead of CJXL_DISTANCE."""
    with _SCAN_PLAN_LOCK:
        return [Path(t) for t in tiff_paths
                if os.path.normcase(str(t)) in _EIGHT_BIT_PLAN]


def _log_eight_bit_at_other_distance(paths, done: bool) -> None:
    """Before the first encode (done=False: what this run will write) and in
    the summary (done=True: what it wrote), like the film-scan notice."""
    if not paths:
        return
    d = _eight_bit_distance()
    _d = f"{d:g}{' (lossless)' if d == 0 else ''}"
    logger.info(f"8-bit TIFFs: {len(paths)} TIFF(s) {'encoded' if done else 'to encode'} "
                f"at distance {_d} instead of {CJXL_DISTANCE:g} (EIGHT_BIT_DISTANCE "
                f"{EIGHT_BIT_DISTANCE}) — an 8-bit TIFF is small lossless; "
                f"--eight-bit-distance same keeps the run's distance")


# (normcase(tiff path), page index) -> width*height, filled by
# _analyze_tiff_pages for the worker memory cap (process_group).
_PAGE_PIXELS = {}
# The same key -> samples per pixel, for the Python side of that cap
# (_python_bytes_per_pixel).
_PAGE_CHANNELS = {}
_PAGE_PIXELS_LOCK = threading.Lock()


def _note_subifd_images(tif, tiff_path) -> None:
    """E5 of the 2026-10-08 audit: image data in SubIFDs is never encoded.

    tifffile's `pages` is the main IFD chain only. A TIFF/EP- or DNG-style
    file keeps a small preview in IFD0 and the FULL image in a SubIFD: the
    planner saw only the preview, `--multipage-mode skip/ignore/split_all`
    archived it, and --delete-source deleted the file holding the real image
    (reproduced). The SubIFDs are still not encoded — but the source is
    recorded as having discarded real pages, so no delete gate removes it,
    and the run says so."""
    try:
        has = any(getattr(p, "subifds", None) for p in tif.pages)
    except Exception:
        has = False
    if not has:
        return
    with _multipage_ignored_lock:      # _capped_discard_warning needs it held
        _discarded_real_page_sources.add(os.path.normcase(str(tiff_path)))
        _capped_discard_warning(
            f"SubIFD image(s) NOT encoded | {Path(tiff_path).name} | this TIFF keeps "
            f"image data in SubIFDs (TIFF/EP or DNG layout — the full-size image may "
            f"live there); only the main IFD chain is encoded, so the source will "
            f"NOT be deleted")


def _note_unarchivable_content(tif, tiff_path) -> None:
    """#620: record a TIFF whose layers (Photoshop, tag 37724) or oversized
    private tag (Affinity Photo's document) do not reach the JXL, so no
    delete gate removes it. Also a page whose samples are not 8 or 16 bits
    (#621): convert_one refuses it now, but a master written from it by
    v2.25.2 or earlier holds the values unscaled, and --delete-skipped must
    not take that master as the TIFF's archive."""
    what = None
    try:
        for page in tif.pages:
            if page.tags.get(_LAYERS_TAG) is not None:
                what = "Photoshop layers (TIFF tag 37724)"
                break
            _tags, _why = _private_tiff_tags(tif, page)
            if _why:
                what = f"a private TIFF tag too large to carry ({_why})"
                break
            _bps = page.bitspersample
            _bps = tuple(_bps) if isinstance(_bps, (tuple, list)) else (_bps,)
            if any(int(b) not in (8, 16) for b in _bps):
                what = f"{'/'.join(str(int(b)) for b in _bps)}-bit samples"
                break
    except Exception:
        return
    if not what:
        return
    with _multipage_ignored_lock:      # _capped_discard_warning needs it held
        _unarchived_content_sources[os.path.normcase(str(tiff_path))] = what
        if DELETE_SOURCE:
            _capped_discard_warning(
                f"NOT DELETED | {Path(tiff_path).name} | this TIFF holds {what}, "
                f"which its JXL does not keep as they are: the source will NOT "
                f"be deleted")


def _analyze_tiff_pages(tiff_path: Path):
    """Analyze a TIFF and return lists of real/thumbnail page indices plus metadata.

    Returns (real_pages, thumb_pages, page_info) where page_info is a dict
    mapping page index to _page_facts (subfiletype, samples, photometric,
    extrasamples; page 0 also software, ICC class and bit depth) for
    _thumbnails_kept and _scan_kind.
    """
    page_info = {}
    with tifffile.TiffFile(str(tiff_path)) as tif:
        real_pages = []
        thumb_pages = []
        _note_subifd_images(tif, tiff_path)
        _note_unarchivable_content(tif, tiff_path)
        for i, page in enumerate(tif.pages):
            page_info[i] = _page_facts(i, page)
            try:
                _px = int(page.imagewidth or 0) * int(page.imagelength or 0)
            except (TypeError, ValueError):
                _px = 0
            with _PAGE_PIXELS_LOCK:
                _PAGE_PIXELS[(os.path.normcase(str(tiff_path)), i)] = _px
                _PAGE_CHANNELS[(os.path.normcase(str(tiff_path)), i)] = int(
                    page.samplesperpixel or 3)
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


def _thumbnails_kept(thumbnail_mode: str, page_info: dict = None) -> bool:
    """Whether --thumbnail-mode keeps this TIFF's thumbnail pages — pure.

    page_info is _analyze_tiff_pages' dict; without it (or without a
    'software' entry) the Software tag reads as empty: "auto" then keeps the
    thumbnail and "scans" keeps it only for an IR/mask page.
    """
    tm = (thumbnail_mode or THUMBNAIL_MODE).lower()
    if tm == "include":
        return True
    if tm not in ("auto", "scans"):
        return False
    info = page_info or {}
    software = str((info.get(0) or {}).get('software') or "").casefold()
    if tm == "auto":
        return not any(str(name).casefold() in software
                       for name in THUMBNAIL_DROP_SOFTWARE if str(name).strip())
    # "scans": the same film-scan rule SCAN_DISTANCE uses.
    return _scan_kind(info) is not None


# Photometrics convert_one encodes (MINISBLACK, RGB); everything else it
# rejects with a per-file error.
_ENCODABLE_PHOTOMETRICS = frozenset({1, 2})


def _kept_thumbnail_pages(thumb_pages: list, thumbnail_mode: str,
                          page_info: dict = None) -> list:
    """The thumbnail pages --thumbnail-mode encodes — pure.

    "auto"/"scans" leave out a thumbnail they could only fail on: the decoder
    itself appends a YCbCr JPEG preview to every single-page TIFF it writes,
    and re-archiving such a TIFF must neither error on it nor turn it into a
    _thumbnail.jxl. "include" keeps every page, as before.
    """
    tm = (thumbnail_mode or THUMBNAIL_MODE).lower()
    if not _thumbnails_kept(tm, page_info):
        return []
    if tm == "include":
        return list(thumb_pages)
    info = page_info or {}
    return [i for i in thumb_pages
            if (info.get(i) or {}).get('photometric', 2) in _ENCODABLE_PHOTOMETRICS]


def _planned_page_names(stem: str, real_pages: list, thumb_pages: list,
                        mp_mode: str = None, thumbnail_mode: str = None,
                        thumbnail_suffix: str = None, page_info: dict = None) -> list:
    """The JXL file names convert_multipage writes for one TIFF, given its
    page classification (_analyze_tiff_pages) — pure: no logging, no
    counters, no file access.

    Mirrors convert_multipage's page selection and naming branch by branch
    (tests/test_audit_261008_leftovers.py pins the two together). The
    wrapper's manifest collision scan calls it to see a split page
    (photo_page1.jxl) landing on another entry's photo_page1.tif output (W9
    of the 2026-10-08 audit). Unset arguments read this module's settings.
    page_info (from _analyze_tiff_pages) lets "auto"/"scans" decide; the
    planner's sync guard, which reads the disk, is not mirrored — naming one
    thumbnail too many only makes the collision scan more careful."""
    mp = (mp_mode or MULTIPAGE_TIFF_MODE).lower()
    tm = (thumbnail_mode or THUMBNAIL_MODE).lower()
    suffix = THUMBNAIL_SUFFIX if thumbnail_suffix is None else thumbnail_suffix
    if mp == "ignore":
        return [f"{stem}.jxl"]
    pages = []
    if mp == "skip":
        if len(real_pages) > 1 or (not real_pages and not thumb_pages):
            return []
        if len(real_pages) == 1:
            pages.append((real_pages[0], real_pages[0] in thumb_pages))
        else:
            pages.append((0, 0 in thumb_pages))
    elif mp == "split":
        pages += [(i, False) for i in real_pages]
        pages += [(i, True) for i in _kept_thumbnail_pages(thumb_pages, tm, page_info)]
    elif mp == "split_all":
        pages += [(i, False) for i in real_pages]
        pages += [(i, True) for i in thumb_pages]
    else:
        pages.append((0, 0 in thumb_pages))
    single = len(pages) == 1 and not pages[0][1]
    names = []
    for idx, is_thumb in pages:
        if single:
            names.append(f"{stem}.jxl")
            continue
        base = stem if idx == 0 else f"{stem}_page{idx}"
        names.append(base + (suffix if is_thumb else "") + ".jxl")
    return names

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
# Projecting the preflight from a distance below the installed cjxl's floor
# would model a file that cjxl will never write — see _MIN_EFFECTIVE_DISTANCE
# in jxl_photo_common.py.
_MIN_EFFECTIVE_DISTANCE = _common._MIN_EFFECTIVE_DISTANCE
_MIN_EFFECTIVE_DISTANCE_PRE_012 = _common._MIN_EFFECTIVE_DISTANCE_PRE_012


_min_effective_distance = _common._min_effective_distance

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
        # The estimate must match the real encode path: lossy modular files
        # run up to a third larger, so estimating with VarDCT would
        # under-report the space a --modular run needs.
        if CJXL_MODULAR and distance > 0:
            cmd.append("--modular=1")
        r = _run_captured(cmd, 300)
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
    def _size_or_zero(p: Path) -> int:
        try:
            return p.stat().st_size
        except OSError:
            # Vanished between the scan and this preflight (TOCTOU): sort it as
            # empty instead of letting one missing file kill the WHOLE estimate
            # — the OSError was caught by main's catch-all at debug level and
            # the projection silently vanished.
            return 0

    uniq = sorted({Path(t) for t in tiffs}, key=_size_or_zero)
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


def _preflight_groups(groups: Dict[Path, list]) -> Dict[Path, list]:
    """The part of the plan this run will WRITE, for the space preflight.

    An up-to-date output is a SKIP and needs no space: a nightly sync over a
    whole library used to sample three crops and project the space of every
    TIFF in it (193 GB for a run that re-encoded 164 of 1 344 photos). The
    check is the same mtime comparison convert_one makes (_would_skip), so it
    costs a few stat calls the overwrite guard has already paid for."""
    out = {}
    for dest, items in groups.items():
        todo = [it for it in items if not _would_skip(it[0], it[1])]
        if todo:
            out[dest] = todo
    return out


def _preflight_space(groups: Dict[Path, list], distance: float, effort: int,
                     staging_dir) -> None:
    """Warn -- never block -- when the projected output will not fit.

    Advisory on purpose. The estimate is measured but still an estimate, and a
    projection that REFUSED a run would eventually refuse one that fits. What
    stops a run stays evidence-based (see the disk-full abort): a real failure
    on a volume that is really full.
    """
    per_group = {}
    # Sources encoded at ANOTHER distance than the run's (film scans under
    # SCAN_DISTANCE, lossless by default) are counted at their full size —
    # a lossless JXL stays under its TIFF — and kept out of the sample crops,
    # which measure the run's distance.
    other_d = {}
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
        other_d[dest] = sum(n for t, n in srcs.items() if _distance_for(t) != distance)
    total_in = sum(per_group.values())
    if total_in < _PREFLIGHT_MIN_BYTES:
        return

    floor = _min_effective_distance(_get_cjxl_cmd() or "cjxl")
    effective = max(distance, floor) if distance > 0 else 0.0
    logger.info(f"Preflight: measuring compression on {_SAMPLE_COUNT} sample crop(s)...")
    _sampled = [t for items in groups.values() for t, *_ in items
                if _distance_for(t) == distance]
    ratio, samples = _measure_batch_ratio(
        _sampled or [t for items in groups.values() for t, *_ in items], effective, effort)
    if ratio is None:
        logger.info("Preflight: could not measure a ratio; skipping the space estimate.")
        return
    if ratio > 0:
        # Turn the full-size sources into "input bytes" the ratio maps back
        # to their own size (ratio x bytes/ratio = bytes).
        for dest in per_group:
            per_group[dest] += other_d[dest] * (1.0 / ratio - 1.0)
    detail = ", ".join(f"{n}: {r*100:.0f}%" for n, r in samples)
    logger.info(f"Preflight: worst measured ratio {ratio*100:.0f}% of source ({detail})")

    def _volume(p):
        try:
            rp = Path(p).resolve()
        except OSError:
            rp = Path(p)
        return (os.path.splitdrive(str(rp))[0] or str(rp.anchor) or str(rp)).upper()

    def _free_bytes(p):
        """Free space on the volume holding `p`, or None.

        Output folders usually do NOT exist yet at preflight time (the children
        create them per group), and disk_usage() raises on a missing path — so
        the destination line was silently dropped on exactly the first run,
        when the estimate matters most. Walk up to the nearest existing
        ancestor: it is on the same volume, which is all this measures.
        """
        probe = Path(p)
        for candidate in (probe, *probe.parents):
            try:
                return shutil.disk_usage(str(candidate)).free
            except OSError:
                continue
        return None

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
        free = _free_bytes(staging_dir)
        if free is not None:
            verdict = "OK" if free > peak else "NOT ENOUGH"
            line = (f"Preflight: staging {staging_dir} -- {_fmt_size(free)} free, "
                    f"peak ~{_fmt_size(peak)} ({verdict})")
            logger.warning(line) if free <= peak else logger.info(line)

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
        free = _free_bytes(probe)
        if free is None:
            continue
        tight = free <= need
        if not tight and staging_vol is not None and vol == staging_vol:
            continue
        line = (f"Preflight: destination {vol or probe} -- {_fmt_size(free)} free, "
                f"needs ~{_fmt_size(need)} ({'NOT ENOUGH' if tight else 'OK'})")
        logger.warning(line) if tight else logger.info(line)


# --- Round-trip verification (opt-in delete gate) --------------------------
# A 45 MP RGB page is ~550 MB as uint16 and the real film scans are bigger, so
# every step here is written to avoid materialising a second full-size float
# copy: the statistics are accumulated over row blocks instead.
_VERIFY_CHUNK_ROWS = 512


# --- Provenance: which source made this output -----------------------------
# The code of this section has ONE copy, in jxl_photo_common.py (AGENTS.md,
# "The common module"); the names below bind it here. Fix it there.
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

# Pairs (normcased TIFF path, normcased JXL path) this run PROVED to be the same
# bytes: the TIFF is newer than its JXL but its sha256 is the `jxlphoto-srcsum`
# the JXL carries (a backup restored with a new date). Filled by
# _mark_sync_same_bytes before anything reads _would_skip; run-scoped.
_SYNC_SAME_BYTES: set = set()


def _sync_same_bytes(tiff_path, final_path) -> bool:
    """Did this run prove that `final_path` was made from the bytes `tiff_path`
    holds now? Only ever True after _mark_sync_same_bytes read both sides."""
    return (os.path.normcase(str(tiff_path)),
            os.path.normcase(str(final_path))) in _SYNC_SAME_BYTES


def _prove_same_bytes(tiff_path, final_path) -> bool:
    """_sync_same_bytes, and when it is not known yet, ONE proof now: the
    srcsum of `final_path` against the sha256 of `tiff_path` (marks the pair
    on success). Only for a caller that runs before _mark_sync_same_bytes
    (the thumbnail guard, during page analysis). Never True on a doubt."""
    if _sync_same_bytes(tiff_path, final_path):
        return True
    if OVERWRITE != "smart":
        return False
    try:
        info = _read_source_markers_batch([Path(final_path)])[str(final_path)]
        want = info.get("srcsum")
        if info.get("unread") or not want or _file_content_id(tiff_path) != want:
            return False
    except OSError:
        return False
    _SYNC_SAME_BYTES.add((os.path.normcase(str(tiff_path)),
                          os.path.normcase(str(final_path))))
    return True


def _file_digest_cached(*a, **k): return _common._file_digest_cached(_G, *a, **k)


def _file_content_id(*a, **k): return _common._file_content_id(_G, *a, **k)


def _read_source_markers_batch(*a, **k): return _common._read_source_markers_batch(_G, *a, **k)


def _provenance_ok(*a, **k): return _common._provenance_ok(_G, *a, **k)


def _provenance_proves_archive(*a, **k): return _common._provenance_proves_archive(_G, *a, **k)


def _provenance_marker_args(*a, **k): return _common._provenance_marker_args(_G, *a, **k)


def _rewrite_master_relation(jxl_path: Path, arg_lines: list, expect_src: str):
    """Apply exiftool `arg_lines` to an EXISTING master without risking it.
    Returns None on success, else the reason (the master is then untouched).

    A temp copy beside it gets the edit, the box reorder (exiftool appends its
    boxes after the codestream — IrfanView would lose the EXIF) and the
    integrity check; the jxlphoto-src read back must be `expect_src`. Then the
    master's own mtime is restored on the temp and it replaces the master with
    an atomic same-folder os.replace. Keeping the mtime matters: derivative
    runs (--sync) re-derive every master that looks newer."""
    jxl_path = Path(jxl_path)
    tmp = jxl_path.parent / f"{uuid.uuid4().hex}_{jxl_path.name}.tmp"
    try:
        st = jxl_path.stat()
        shutil.copy2(str(jxl_path), str(tmp))
        r = _run_exiftool_argfile(["-overwrite_original"] + list(arg_lines) + [str(tmp)],
                                  timeout=EXIFTOOL_TIMEOUT)
        if r.returncode != 0:
            return f"exiftool: {(r.stderr or '').strip()[:200]}"
        reorder_jxl_boxes(tmp)
        if not _verify_jxl_integrity(tmp):
            return "the rewritten file failed the JXL integrity check"
        got = (_read_source_markers_batch([tmp]).get(str(tmp)) or {}).get("src")
        if got != expect_src:
            return f"the record read back is {got!r}, expected {expect_src!r}"
        os.utime(str(tmp), ns=(st.st_atime_ns, st.st_mtime_ns))
        if _changed_since(jxl_path, st):
            return ("the master changed while its record was being rewritten (another "
                    "run?) — left untouched")
        os.replace(str(tmp), str(jxl_path))
        _refresh_planned_output(jxl_path)
        return None
    except Exception as e:
        return str(e)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _restamp_moved(all_items, dry_run):
    """--restamp-moved: rewrite the jxlphoto-src of masters whose folder was
    renamed or moved, leaving the pixels alone.

    For every planned output whose recorded path id is not this source's but
    whose recorded CONTENT id matches this source's bytes, the record is
    rewritten through _rewrite_master_relation. Anything that does not match
    on content is listed and left alone: it was renamed AND edited, or it is
    another photo. Nothing is encoded, nothing is deleted, no History entry
    is written (this is not a conversion).

    Returns (rc, restamped, failures): rc as --restamp-moved exits with;
    restamped maps each output Path rewritten (or, in a dry run, to be
    rewritten) to its new path id; failures lists (tiff, "restamp failed: ...")
    for every failed rewrite. --restamp-then-sync uses the last two."""
    outs = sorted({j for _t, j, *_ in all_items if j.exists()})
    not_encoded = len({j for _t, j, *_ in all_items}) - len(outs)
    if outs:
        logger.info(f"Restamp: reading the provenance record of {len(outs)} existing output(s)...")
    marks = _read_source_markers_batch(outs)
    restamped = already = differs = unmarked = unread = failed = 0
    restamped_map, failures = {}, []
    _differs_list = []
    _seen = set()
    for t, j, *_rest in all_items:
        if j not in outs or str(j) in _seen:
            continue
        _seen.add(str(j))
        info = marks.get(str(j)) or {"src": None, "srcsum": None}
        new = _source_path_id(t)
        if info.get("src") == new:
            already += 1
            continue
        if info.get("unread"):
            # X-A: not "without a record" — nothing was read, nothing is touched.
            unread += 1
            continue
        if not (info.get("src") or info.get("srcsum")):
            unmarked += 1
            continue
        if info.get("srcsum"):
            try:
                if info["srcsum"] == _file_content_id(t):
                    if dry_run:
                        logger.info(f" DRY | RESTAMP | {j.name}")
                        restamped += 1
                        restamped_map[j] = new
                        continue
                    lines = []
                    if info.get("src"):
                        lines.append(f"-XMP-dc:Relation-={SRC_PREFIX}{info['src']}")
                    lines.append(f"-XMP-dc:Relation+={SRC_PREFIX}{new}")
                    why = _rewrite_master_relation(j, lines, new)
                    if why is None:
                        logger.info(f"RESTAMP | {j.name}: record now names {t}")
                        restamped += 1
                        restamped_map[j] = new
                    else:
                        logger.error(f"RESTAMP FAILED | {j.name}: {why}")
                        failed += 1
                        failures.append((str(t), f"restamp failed: {why}"))
                    continue
            except OSError as e:
                logger.error(f"RESTAMP FAILED | {j.name}: {e}")
                failed += 1
                failures.append((str(t), f"restamp failed: {e}"))
                continue
        differs += 1
        _differs_list.append(str(j))
    for _d in _differs_list[:10]:
        logger.warning(f"  NOT restamped | {_d}: the TIFF's content no longer matches "
                       f"this master (renamed AND edited since, or a different photo). "
                       f"Left as it is.")
    if len(_differs_list) > 10:
        logger.warning(f"  ... and {len(_differs_list) - 10} more")
    if _differs_list:
        logger.warning("  For these: delete the old JXL by hand (the TIFF is kept) and "
                       "re-run, or run the sync with --no-overwrite-guard. Details: "
                       + GUARD_DOC_URL)
    logger.info(f"Restamp: {restamped} "
                f"{'would be rewritten' if dry_run else 'rewritten'}, "
                f"{already} already correct, {differs} content differs (not touched), "
                f"{unmarked} without a record (see --provenance adopt), "
                + (f"{unread} unreadable (exiftool could not read the record; "
                   f"not touched), " if unread else "")
                + f"{not_encoded} not encoded yet, {failed} failed")
    return (1 if failed else 0), restamped_map, failures


def _read_origin_inputs(*a, **k): return _common._read_origin_inputs(_G, *a, **k)


_origin_for_output = _common._origin_for_output


has_jbrd_box = _common.has_jbrd_box


def _foreign_overwrite_reason(info: dict, src_paths, out_path) -> Optional[str]:
    """Why an EXISTING output this run is about to OVERWRITE must be refused,
    or None when the overwrite may go ahead.

    The #268 guard used to run only when THIS run deleted its sources in a
    collapsing mode. The loss it prevents happens on the OVERWRITE, though: an
    earlier run already deleted the other photo's TIFF, so a later plain sync
    (every scheduled preset is one) destroyed that photo's only file. And every
    mode collapses names — foto.tif and foto.tiff both write foto.jxl.

    Refused: a provenance marker that names ANOTHER source (with --provenance
    content, a matching source-bytes id still accepts it), and a jbrd JXL —
    the transcoder's lossless JPEG archive, which this encoder never writes.
    A markerless output keeps the old behaviour: there is no proof either way,
    and refusing would block every archive written before the markers existed.
    An UNREADABLE one (`unread`: the batch failed) is refused while the guard
    is on — "cannot tell" is not "markerless" (X-A).
    """
    if info.get("src") or info.get("srcsum"):
        if _provenance_ok(info, src_paths, PROVENANCE_CHECK):
            return None
        if not OVERWRITE_GUARD:
            logger.warning(f"  OVERWRITE (overwrite guard off) | {Path(out_path).name}: "
                           f"its provenance marker names another source path "
                           f"(renamed/moved folder, or a different photo)")
            return None
        return "it was made from a different source (its provenance marker names another file)"
    if info.get("unread"):
        # X-A (2026-11-03 audit): the batch that should have read this file's
        # markers failed or stalled. "Cannot tell" used to read as "no
        # marker" and the overwrite went ahead — onto what may be the only
        # copy of another photo. With the guard off the worst case is a
        # foreign marker, which the guard-off run overwrites anyway.
        if not OVERWRITE_GUARD:
            logger.warning(f"  OVERWRITE (overwrite guard off) | {Path(out_path).name}: "
                           f"its provenance markers could not be read")
        else:
            return ("its provenance markers could not be read (the exiftool batch "
                    "failed or stalled), so nothing proves it is not another photo's "
                    "archive — re-run once exiftool reads the folder again")
    if has_jbrd_box(out_path):
        return ("it is a lossless JPEG archive (jbrd) written by the JPEG "
                "transcoder — the only copy of that JPEG")
    return None


def _mark_sync_same_bytes(items: list, dry_run: bool) -> int:
    """Smart sync: a TIFF newer than its JXL but with the SAME BYTES the JXL
    was made from is up to date, not a new file (2.3 of the 2026-11-10 review).

    A TIFF restored from a backup has a new date and the old bytes; the date
    rule alone re-encoded it, and under a larger distance than the master's
    that swapped a good master for a worse one. The master already carries the
    proof -- `jxlphoto-srcsum`, the sha256 of the TIFF it was made from -- so the
    pair is marked here and _would_skip / convert_one / _thumbnail_sync_guard
    answer SKIP for it.

    Fail closed, always the old behaviour on a doubt: a master without a
    readable srcsum (older, markerless, jbrd, an unread batch) or a TIFF that
    cannot be read is NOT marked and is re-encoded as before. The path marker
    (`jxlphoto-src`) is not consulted: a moved folder with the same bytes is
    still up to date, and a SKIP writes nothing for the overwrite guard to
    judge. Writes nothing, not even the master's date (see the end of the
    function). `dry_run` is kept for the caller's signature. Returns how many
    pairs were marked.
    """
    if OVERWRITE != "smart":
        return 0
    cands = []
    seen = set()
    for it in items:
        t, j = it[0], it[1]
        key = (os.path.normcase(str(t)), os.path.normcase(str(j)))
        if key in seen:
            continue
        seen.add(key)
        try:
            if not j.exists() or t.stat().st_mtime <= j.stat().st_mtime:
                continue
        except OSError:
            continue
        cands.append((t, j))
    if not cands:
        return 0
    logger.info(f"Sync: comparing {len(cands)} newer TIFF(s) with the bytes their "
                f"JXL was made from...")
    # Pairs the thumbnail guard already proved while the pages were analysed
    # need no second read.
    todo = [(t, j) for t, j in cands if not _sync_same_bytes(t, j)]
    marks = _read_source_markers_batch(sorted({str(j) for _t, j in todo})) if todo else {}
    prog = _batch_read_state("Sync byte check", len(cands))
    proven = []
    for n, (t, j) in enumerate(cands):
        _batch_read_tick(prog, n)
        if _sync_same_bytes(t, j):
            try:
                proven.append((t, j, t.stat().st_mtime_ns))
            except OSError:
                pass
            continue
        info = marks.get(str(j)) or {}
        want = info.get("srcsum")
        if info.get("unread") or not want:
            continue
        try:
            if _file_content_id(t) != want:
                continue
            tiff_mtime_ns = t.stat().st_mtime_ns
        except OSError:
            continue
        _SYNC_SAME_BYTES.add((os.path.normcase(str(t)), os.path.normcase(str(j))))
        proven.append((t, j, tiff_mtime_ns))
    if proven:
        logger.info(f"Sync: {len(proven)} of {len(cands)} newer TIFF(s) are the same "
                    f"bytes — kept as up to date")
    else:
        logger.info(f"Sync: the {len(cands)} newer TIFF(s) are not the bytes their "
                    f"JXL was made from (or it records no proof): re-encoded")
    # Nothing is written, in a real run or a dry one. The master's date is left
    # alone ON PURPOSE (review of #638): the recompressor's own sync compares a
    # master's mtime with its derivatives', so moving it forward would
    # re-derive every copy made from it. The price is that a run after a
    # date-changing restore compares the newer TIFFs again; that is a read, not
    # an encode.
    return len(proven)


def _skipped_archive_proof(pairs: list) -> dict:
    """E-1 proof for --delete-skipped: does each pre-existing output carry the
    provenance markers of the VERY source behind it?

    The source is the master TIFF, so there is no marker on the source side to
    compare against (unlike the recompressor, whose source JXLs carry the pair
    verbatim): `_provenance_proves_archive` recomputes the proof from the TIFF
    itself — the recorded path id (or, with --provenance content, the
    source-bytes id) must match, AND the recorded source-bytes id must match
    the TIFF as it is NOW. The path alone is the overwrite guard's question;
    a TIFF replaced in place by an older-dated different file still matches
    it, smart sync skips it, and the stale archive deleted the new version.
    Fail closed: a markerless legacy archive, an unreadable batch, a foreign
    marker or a changed TIFF all read False → the source is KEPT (heal by
    re-encoding, or by --provenance adopt in a collapsing mode).

    Returns {(tiff_path, final_path) -> bool}; keys are normcased to line up
    with the string paths the delete gate and the preview carry.
    """
    outputs = sorted({str(dst) for _src, dst in pairs})
    marks = _read_source_markers_batch([Path(o) for o in outputs])
    proof = {}
    for src, dst in pairs:
        info = marks.get(str(dst)) or {"src": None, "srcsum": None}
        proof[(os.path.normcase(str(src)), os.path.normcase(str(dst)))] = \
            _provenance_proves_archive(info, Path(src), PROVENANCE_CHECK)
    return proof


def _canon_for_compare(*a, **k): return _common._canon_for_compare(_G, *a, **k)


def _decode_jxl_for_verify(*a, **k): return _common._decode_jxl_for_verify(_G, *a, **k)


def _compare_stats(*a, **k): return _common._compare_stats(_G, *a, **k)


def _verify_roundtrip_page(tiff_path: Path, page_idx: int, jxl_path: Path,
                           distance: float):
    """Does `jxl_path` really hold page `page_idx` of `tiff_path`?

    Returns (ok, detail). Any failure to even run the check returns False: this
    gates an irreversible delete, so "could not tell" must mean "keep".
    """
    try:
        with tempfile.TemporaryDirectory(prefix="jxl_verify_", dir=TEMP_DIR) as tmp:
            dec = _decode_jxl_for_verify(jxl_path, Path(tmp),
                                         require_original_space=distance > 0)
        with tifffile.TiffFile(str(tiff_path)) as tif:
            if page_idx >= len(tif.pages):
                return False, f"source page {page_idx} no longer exists"
            src = tif.pages[page_idx].asarray()
    except Exception as e:
        return False, f"could not verify: {e}"

    src, dec = _canon_for_compare(src), _canon_for_compare(dec)
    if src.shape != dec.shape:
        return False, f"shape mismatch: source {src.shape} vs decoded {dec.shape}"

    if distance == 0:
        # Lossless means lossless. No tolerance, no statistics.
        if np.array_equal(src, dec):
            return True, "pixel-identical"
        return False, "lossless encode did not decode back pixel-identical"

    s_mean, d_mean, mse = _compare_stats(src, dec)
    ratio = (d_mean / s_mean) if s_mean > 0 else (1.0 if d_mean == 0 else 0.0)
    psnr = float("inf") if mse <= 0 else 10.0 * math.log10((65535.0 ** 2) / mse)
    # Bounded on BOTH sides: the documented scanner-ICC failure darkens the
    # image, but a decode that blows out is just as wrong.
    lo = VERIFY_LOSSY_MIN_MEAN_RATIO
    hi = 1.0 / lo if lo > 0 else float("inf")
    detail = f"mean ratio {ratio:.3f}, PSNR {psnr:.1f} dB"
    if not (lo <= ratio <= hi):
        return False, f"{detail} — brightness is not the source's"
    if psnr < VERIFY_LOSSY_MIN_PSNR:
        return False, f"{detail} — below the {VERIFY_LOSSY_MIN_PSNR} dB floor"
    return True, detail


def _would_skip(tiff_path: Path, final_path: Path) -> bool:
    """Would convert_one report SKIP for this pair?

    Mirrors the decision at the top of convert_one exactly — including its
    TOCTOU fallback, where an unreadable stat means "stale, convert it". Used
    by the dry-run preview of --delete-skipped (a destructive option that
    could not be previewed would be the wrong kind of opt-in), by the
    overwrite guard and by the space preflight (_preflight_groups).

    Smart sync also skips a TIFF that is newer but PROVED to be the same bytes
    its JXL was made from (_mark_sync_same_bytes, filled once per run before
    the plan is judged) -- a pure lookup here, never a hash.
    """
    if not final_path.exists():
        return False
    if OVERWRITE is False:
        return True
    if OVERWRITE == "smart":
        try:
            if tiff_path.stat().st_mtime <= final_path.stat().st_mtime:
                return True
        except OSError:
            return False
        return _sync_same_bytes(tiff_path, final_path)
    return False        # OVERWRITE True: always reconvert, never a skip


def _plan_would_delete_source(all_items: list) -> bool:
    """Would this plan even ATTEMPT a source deletion? The gates only delete
    for real conversions — or, under --delete-skipped, for the already-
    archived ones.

    The confirmation used to run before the plan was consulted at all: a
    no-TTY re-run of an already-archived folder (--delete-source, no
    --delete-skipped) was asked for a token it could not answer, exited 3, and
    would exit 3 FOREVER — without a single deletion pending (bug #432's
    decoder twin). Fail closed: any doubt (an item this run would write, or a
    deletion-eligible skip) keeps the prompt charged.
    """
    for _t, _j, *_ in all_items:
        if not _would_skip(_t, _j):
            return True
        if DELETE_SKIPPED:
            return True
    return False


def convert_one(tiff_path: Path, write_path: Path, final_path: Path, page_idx: int = 0,
                is_thumbnail: bool = False, subfiletype: int = 0, samples: int = 3,
                multipage_group: str = None, multipage_total: int = None):
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
    multipage_total: how many JXLs the split produced. Written alongside the
                     group id so the decoder can tell a complete group from a
                     truncated one (see PAGES_XMP_PREFIX).
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
            if _sync_same_bytes(tiff_path, final_path):
                n, total = next_count()
                logger.info(f"[{n}/{total}] SKIP (sync: same TIFF bytes, JXL kept) | {tiff_path.name}")
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

    # Never write under the final name without staging: a run killed
    # externally (idle-timeout kill, Ctrl+C, power loss) would leave a
    # TRUNCATED file at the final path with a NEW mtime, which the next
    # smart-sync run then treats as up to date forever. Write to a uuid temp
    # BESIDE the final and swap it in with an atomic same-folder os.replace
    # only after the integrity check passed — so the final name only ever
    # names a verified, complete file.
    _promote_local = write_path == final_path
    if _promote_local:
        # ".tmp, not .jxl" (same rule the transcoder's repair-jbrd got in
        # v2.1.1): a kill between write and promote would leave the temp
        # BESIDE the final with a real-sounding name, adoptable as a real
        # input by the next run that scans this folder for JXLs (the decoder,
        # the recompressor). os.replace below doesn't care about the
        # extension, and nothing validates it before promotion.
        write_path = final_path.parent / f"{uuid.uuid4().hex}_{final_path.name}.tmp"

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
            #    - icc_original: unmodified for the jxlphoto-iccprofile token (round-trip preservation)
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
                # MINISWHITE (0): page.asarray() returns the RAW samples, where
                # 0 means WHITE, and the JXL comes back MINISBLACK — the
                # archived image is tonally INVERTED, silently, and the source
                # stays deletable by --delete-source. Reject like the other
                # photometrics instead of inverting on read: TIFFs with a
                # predictor or partial palettes make a sample flip risky.
                if photometric == tifffile.PHOTOMETRIC.MINISWHITE:
                    _log_rejected_file(str(tiff_path), "MINISWHITE (white=0) not supported")
                    raise ValueError(
                        "TIFF photometric MINISWHITE (white=0) is not supported. "
                        "Invert to MINISBLACK first.")
                # Lab-family and YCbCr photometrics: tifffile returns the RAW
                # samples for these, so encoding them as RGB silently
                # reinterprets the colors (a Lab master archives as RGB numbers
                # and never comes back). Reject like CMYK/PALETTE instead of
                # writing a false color model into the archive.
                if photometric in (tifffile.PHOTOMETRIC.CIELAB,
                                   tifffile.PHOTOMETRIC.ICCLAB,
                                   tifffile.PHOTOMETRIC.ITULAB,
                                   tifffile.PHOTOMETRIC.YCBCR):
                    _log_rejected_file(str(tiff_path), f"{photometric.name} not supported")
                    raise ValueError(
                        f"TIFF photometric {photometric.name} is not supported. "
                        f"Convert to RGB first.")
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
                # The role of a 2nd/4th channel (EXTRASAMPLES_XMP_PREFIX):
                # read here, while the page is open; written as a marker below.
                try:
                    _extrasamples = tuple(int(v) for v in (page.extrasamples or ()))
                except (TypeError, ValueError):
                    _extrasamples = ()
                # Private tags exiftool cannot copy (TIFFTAG_XMP_PREFIX).
                _private_tags, _private_why = _private_tiff_tags(tif, page)
                if _private_why and not STRIP_METADATA:
                    logger.warning(
                        f"  >{tiff_path.name} (page {page_idx}): its private TIFF "
                        f"tags are not kept — {_private_why}")
                _iptc_note = _iptc_left_out_note(page)
                if _iptc_note and not STRIP_METADATA:
                    logger.warning(f"  >{tiff_path.name} (page {page_idx}): {_iptc_note}")
                # The Photoshop block that is too big to carry: every
                # Lightroom/Photoshop TIFF has one (a preview), so it is one
                # line at the end of the run — but a saved path is said here.
                _ps_note, _ps_paths = _photoshop_left_out_note(tif, page)
                if _ps_note and not STRIP_METADATA:
                    with _multipage_ignored_lock:
                        _photoshop_left_out["files"] += 1
                    if _ps_paths:
                        logger.warning(f"  >{tiff_path.name} (page {page_idx}): {_ps_note}")
                    else:
                        logger.debug(f"  >{tiff_path.name} (page {page_idx}): {_ps_note}")
                # IPTC -> XMP fill (_fill_xmp_from_iptc): decided from the tag
                # of page 0, no exiftool call to find out; every page of a
                # split gets it (same source, same XMP).
                _iptc_fill = not STRIP_METADATA and _has_iptc(tif)
                img = page.asarray()
                # Reject float/double and other unsupported integer dtypes before casting.
                if img.dtype.kind == 'f':
                    _log_rejected_file(str(tiff_path), f"float dtype {img.dtype} not supported")
                    raise ValueError(f"Unsupported TIFF dtype: {img.dtype}. Float TIFFs are not supported.")
                if img.dtype not in (np.uint8, np.uint16):
                    _log_rejected_file(str(tiff_path), f"dtype {img.dtype} not supported")
                    raise ValueError(f"Unsupported TIFF dtype: {img.dtype}. Expected uint8 or uint16.")
                # #621: 10/12-bit (or 2/4-bit) samples arrive unscaled in a uint16/uint8
                # array; archived as they are, the image comes back 16x darker.
                _bps = page.bitspersample
                _bps = tuple(_bps) if isinstance(_bps, (tuple, list)) else (_bps,)
                _want = 8 if img.dtype == np.uint8 else 16
                if any(int(b) != _want for b in _bps):
                    _shown = "/".join(str(int(b)) for b in _bps)
                    _log_rejected_file(str(tiff_path), f"{_shown}-bit samples not supported")
                    raise ValueError(f"Unsupported TIFF bit depth: {_shown} bits per sample. "
                                     f"Expected 8 or 16.")
                # Capture original bit depth before converting to 16-bit for the JXL pipeline.
                original_depth = 8 if img.dtype == np.uint8 else 16
                # Convert 8-bit to 16-bit with proper scaling (multiply by 257).
                # One array, never a copy of a page that already is uint16
                # (#569: each copy is 270 MB per worker on a 45 MP page).
                if img.dtype == np.uint8:
                    img = np.multiply(img, 257, dtype=np.uint16)  # 0-255 -> 0-65535
                else:
                    img = img.astype(np.uint16, copy=False)
            # Grayscale detection is driven ONLY by the ACTUAL array: a 2D
            # array or a single-channel 3D array is grayscale; a 2-channel
            # array is gray+alpha (LA). The planning-time samples-per-pixel is
            # deliberately NOT consulted — if metadata said 1 channel but the
            # pixels are RGB, flagging the JXL as grayscale would make the
            # decoder discard G and B.
            # Provenance ids. Hashed from the FILE, not the decoded pixels, so
            # re-checking later costs a read instead of a decode — and so all
            # three scripts compute the same thing (test_helper_parity).
            _src_path_id = _source_path_id(tiff_path)
            try:
                _src_content_id = _file_content_id(tiff_path)
            except Exception:
                _src_content_id = None    # never fail an encode over a marker

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
                # The page's OWN ICC can still be an RGB profile on grayscale
                # pixels (scanners tag every page with the same RGB profile).
                # cjxl rejects a grayscale PNG carrying an RGB iCCP ("RGB color
                # space not permitted on grayscale PNG"), failing the whole
                # encode; keep the profile for the XMP round-trip only and let
                # the PNG go out without iCCP. The data-space signature sits at
                # header offset 16..
                if (is_grayscale and icc_bytes and len(icc_bytes) >= 20
                        and icc_bytes[16:20] != b"GRAY"):
                    logger.warning(
                        f"  >{tiff_path.name} (page {page_idx}): the page's ICC "
                        f"profile is not grayscale "
                        f"({icc_bytes[16:20].decode('ascii', 'replace')}); keeping "
                        f"it in XMP only (a grayscale PNG cannot carry it)")
                    icc_bytes = None

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
            # This file's distance: SCAN_DISTANCE for a film scan it covers,
            # CJXL_DISTANCE otherwise (_distance_for, decided at planning).
            _dist = _distance_for(tiff_path)
            container_flag = ["--container=1"] if _dist > 0 else []

            # --modular=1 forces the Modular encoder for lossy output.
            # Only applied when CJXL_MODULAR=True and d>0 (lossless always uses Modular).
            # Modular lossy produces 2-3x larger files than VarDCT for photos.
            modular_flag = ["--modular=1"] if (CJXL_MODULAR and _dist > 0) else []

            # Decide whether to put the ICC into the PNG iCCP chunk.
            lossy = _dist > 0
            if not lossy:
                png_icc_bytes = icc_bytes  # lossless: always embed ICC natively
            else:
                embed = should_embed_icc_in_png(icc_bytes, lossy=True, tiff_path=tiff_path,
                                                distance=_dist)
                png_icc_bytes = icc_bytes if embed else None
                if not embed and icc_bytes:
                    logger.info(f"  >Skipping ICC in PNG iCCP for {tiff_path.name} (page {page_idx}); profile will be preserved via XMP")

            # E4 (2026-10-08 audit): a 2nd/4th channel goes to cjxl as ALPHA,
            # and lossy cjxl defaults to --keep_invisible=0 — it rewrites the
            # colour wherever that channel is 0. For an RGB+IR scan stored as
            # 4 channels that is real image under the dust (measured: error
            # 1601 vs 19 elsewhere, ~80x what was asked). Keep it. And with
            # cjxl 0.13+, keep the channel itself: lossy cjxl 0.13 drops an
            # all-opaque alpha by default, and the TIFF came back one channel
            # short (#670, _keep_alpha_flags).
            _channels = img.shape[2] if getattr(img, "ndim", 0) == 3 else 1
            invisible_flag = (["--keep_invisible=1"] + _keep_alpha_flags(_get_cjxl_cmd() or "cjxl")
                              if lossy and _channels in (2, 4) else [])

            if USE_RAM_FOR_PNG:
                png_input = make_png_bytes(img, png_icc_bytes)
                del img
                cjxl_cmd = [_get_cjxl_cmd() or "cjxl", "-", str(write_path), "-d", str(_dist), "--effort", str(CJXL_EFFORT)] + container_flag + modular_flag + invisible_flag + _cjxl_buffering_flag()
                output_dirty = True
                r = _run_captured(cjxl_cmd, CJXL_TIMEOUT, input=png_input)
                del png_input
            else:
                png_path = tmp_dir / f"{tiff_path.stem}.png"
                png_bytes = make_png_bytes(img, png_icc_bytes)
                del img
                png_path.write_bytes(png_bytes)
                del png_bytes
                cjxl_cmd = [_get_cjxl_cmd() or "cjxl", str(png_path), str(write_path), "-d", str(_dist), "--effort", str(CJXL_EFFORT)] + container_flag + modular_flag + invisible_flag + _cjxl_buffering_flag()
                output_dirty = True
                r = _run_captured(cjxl_cmd, CJXL_TIMEOUT)

            if r.returncode != 0:
                err = _stderr_tail(r.stderr)
                raise RuntimeError(f"cjxl: {err}")

            # 6. Build and execute unified metadata injection (CORRECTED - replaces old steps 5+7)
            # This preserves original XMP, adds encoding tags, and embeds ICC if configured
            # Uses icc_original (unmodified) for round-trip preservation
            # The metadata writes, in the order they always ran, each a command
            # of its own (the XMP block before the edits, the edits, the -wm cg
            # IPTC fill, the page markers): _run_exiftool_writes runs them in
            # ONE exiftool process where it can, and a failure raises the
            # message of its own command. Uses icc_original (unmodified) for
            # round-trip preservation.
            _xmp_preloaded = not STRIP_METADATA and bool(xmp_original)
            writes = []        # (command, what its failure says, output quoted)
            if _xmp_preloaded:
                writes.append((_xmp_block_preload_args(xmp_original, write_path),
                               "exiftool XMP copy failed: ", 200))
            inject_args = build_metadata_injection_args(
                tiff_path, write_path, tmp_dir, exif_bin, icc_original, xmp_original,
                original_depth=original_depth,
                strip_metadata=STRIP_METADATA,
                src_path_id=_src_path_id, src_content_id=_src_content_id,
                xmp_preloaded=_xmp_preloaded, distance=_dist
            )
            writes.append((Path(inject_args), "exiftool failed: ", 300))

            # IPTC fields the XMP lacks (the capture date of a SilverFast scan
            # lives only there): a command of its own, before reorder_jxl_boxes.
            if _iptc_fill:
                writes.append((_fill_xmp_from_iptc(tiff_path, write_path, tmp_dir),
                               "exiftool IPTC copy failed: ", 200))

            # Multi-page group marker: tag split pages so the decoder reconstructs
            # only genuinely-split files. Stored as an ADDITIONAL value in the
            # dc:Relation list (a bag), so any Relation the user already had is
            # preserved — unlike a scalar field, appending never overwrites.
            # Absence of the marker means "standalone file" even if the name has _pageN.
            # Skipped entirely under --strip (no metadata = no reconstruction markers).
            # A failed marker write must not be silent: without the marker the
            # decoder can never reconstruct this multi-page TIFF.
            if multipage_group and not STRIP_METADATA:
                relation_args = [
                    "-XMP-dc:Relation+=" + MULTIPAGE_XMP_MARKER + multipage_group,
                    # Page index and thumbnail role are stored as markers,
                    # not inferred from the filename: a source TIFF named
                    # *_page<N> or *_thumbnail would otherwise corrupt the
                    # reconstruction order/naming.
                    "-XMP-dc:Relation+=" + PAGE_XMP_PREFIX + str(page_idx),
                ]
                if multipage_total:
                    relation_args.append(
                        "-XMP-dc:Relation+=" + PAGES_XMP_PREFIX + str(multipage_total))
                if is_thumbnail:
                    relation_args.append("-XMP-dc:Relation+=" + THUMB_XMP_FLAG)
                if icc_inherited and page_idx > 0:
                    relation_args.append("-XMP-dc:Relation+=" + ICC_INHERITED_XMP_FLAG)
                if subfiletype != 0:
                    relation_args.append("-XMP-dc:Relation+=" + SUBFILETYPE_XMP_PREFIX + str(subfiletype))
                writes.append((["-overwrite_original"] + relation_args + [str(write_path)],
                               "exiftool multipage marker write failed: ", 200))

            # A split that yields exactly ONE output from a page > 0 has no
            # group marker (group ids exist only for multi-output splits), but
            # the page is still "page N": the reconstruction facts (inherited
            # ICC, SubfileType) must travel with it or the decoder cannot
            # restore the original tag structure. Written outside the
            # multipage_group block, like the grayscale flag below. The page
            # marker is harmless for standalone files (the decoder only reads
            # it for marked groups) and records the real origin.
            if not multipage_group and not STRIP_METADATA and (
                    page_idx > 0 or subfiletype != (1 if is_thumbnail else 0)):
                # The subfiletype marker is written for page 0 too when the
                # page's role is not the default (0 for a real page, 1 for a
                # thumbnail): a single-page TIFF whose only page is SubFileType=4
                # (MASK) used to sail through both blocks above and the decoder
                # then wrote SubfileType=0 back. Thumbnails are restored as 1
                # regardless, so a thumbnail's own 1 stays unmarked — same rule
                # the multi-page group block already follows.
                solo_args = []
                if page_idx > 0:
                    solo_args.append("-XMP-dc:Relation+=" + PAGE_XMP_PREFIX + str(page_idx))
                    if icc_inherited:
                        solo_args.append("-XMP-dc:Relation+=" + ICC_INHERITED_XMP_FLAG)
                if subfiletype != (1 if is_thumbnail else 0):
                    solo_args.append("-XMP-dc:Relation+=" + SUBFILETYPE_XMP_PREFIX + str(subfiletype))
                writes.append((["-overwrite_original"] + solo_args + [str(write_path)],
                               "exiftool page marker write failed: ", 200))

            # Grayscale marker: must be written for standalone files too, otherwise
            # read_png_to_numpy returns a 3-channel RGB array and the decoder cannot
            # restore the original single-channel TIFF page. The ExtraSamples
            # marker rides in the same exiftool command (standalone files too: a
            # single-page RGB+IR scan is the case it exists for).
            page_flags = []
            if is_grayscale:
                page_flags.append("-XMP-dc:Relation+=" + GRAYSCALE_XMP_FLAG)
            if _channels in (2, 4) and _extrasamples:
                page_flags.append("-XMP-dc:Relation+=" + EXTRASAMPLES_XMP_PREFIX
                                  + ",".join(str(v) for v in _extrasamples))
            _kind = _scan_kind_for(tiff_path)
            if _kind:
                page_flags.append(f"-XMP-dc:Relation+={SCAN_XMP_PREFIX}{_kind}")
            elif _scan_reason_for(tiff_path) == "camera":
                page_flags.append(f"-XMP-dc:Relation+={SCAN_XMP_PREFIX}no")
            for _code, _dtype, _raw in _private_tags:
                page_flags.append(f"-XMP-dc:Relation+={TIFFTAG_XMP_PREFIX}"
                                  f"{_code}:{_dtype}:{_raw.hex()}")
            if page_flags and not STRIP_METADATA:
                writes.append((["-overwrite_original"] + page_flags + [str(write_path)],
                               "exiftool page marker write failed: ", 200))

            # The master is written ONCE after cjxl (#675): the exiftool writes
            # run on a stub (cjxl's boxes, the codestream cut to its first
            # bytes), then the stub's boxes in reorder_jxl_boxes's order, the
            # Explorer thumbnail box and cjxl's codestream go into the master
            # in one pass. Each exiftool command used to rewrite the whole
            # master (48 MB copied to append 8 KB), then the reorder and the
            # thumbnail box rewrote it again: 7 writes, 337 MB for a 48 MB
            # file. Anything the stub route cannot do falls back to those
            # steps on the untouched file, as before.
            _assembled = False
            _thumb_jpeg = _thumb_fp = None
            _stub = tmp_dir / "metadata_stub.jxl"
            _stub_writes = _retarget_exiftool_writes(writes, write_path, _stub)
            if _stub_writes is not None:
                try:
                    _write_metadata_stub(write_path, _stub)
                    _run_exiftool_writes(_stub_writes, EXIFTOOL_TIMEOUT)
                    if not STRIP_METADATA and _explorer_thumbnail_wanted(_dist == 0):
                        _thumb_jpeg, _thumb_fp = _thumbnail_for_assembly(
                            write_path, tmp_dir, _assembled_size(write_path, _stub))
                    _assemble_from_stub(write_path, _stub, _thumb_jpeg, _thumb_fp)
                    _assembled = True
                except Exception as e:
                    _thumb_jpeg = _thumb_fp = None
                    logger.debug(f"  >metadata stub not usable ({e}): written in "
                                 f"place | {write_path.name}")
            if _assembled:
                # The fingerprint check of _add_explorer_thumbnail: the box
                # must name the codestream it was made from.
                if _thumb_jpeg is not None and (
                        _codestream_fingerprint(write_path) != _thumb_fp
                        or _thumbnail_box_fingerprint(write_path) != _thumb_fp):
                    raise RuntimeError("the codestream changed while the Explorer "
                                       "thumbnail was written")
            else:
                _run_exiftool_writes(writes, EXIFTOOL_TIMEOUT)

                # Reorder JXL boxes so Exif/XMP come before the codestream. This must
                # run after all exiftool operations (metadata, thumbnail, markers)
                # because each exiftool edit can re-append boxes after the codestream.
                reorder_jxl_boxes(write_path)

                # Explorer thumbnail (EXPLORER_THUMBNAIL), made from the finished
                # file and put in its own box. Only the files that get one pay for
                # it (lossless by default); a failure only warns.
                if not STRIP_METADATA and _explorer_thumbnail_wanted(_dist == 0):
                    _add_explorer_thumbnail(write_path, tmp_dir)

            # Validate EVERY successful output, not just mode-8 delete gates:
            # cjxl returning 0 does not guarantee a well-formed file (disk
            # full, AV truncation, killed mid-write). A corrupt output marked
            # OK would be skipped forever by the next smart-sync run.
            # (The except handler deletes it via output_dirty.)
            if not _verify_jxl_integrity(write_path):
                raise RuntimeError("cjxl returned 0 but the output failed the JXL integrity check")

            if _promote_local:
                # Atomic same-folder swap (see the _promote_local note above):
                # a partial never carries the final name. A failure raises
                # into the error handler, which deletes the temp.
                _swap_into_place(write_path, final_path)

            n, total = next_count()
            status = "overwrite" if overwritten else "ok"
            label  = "OVERWRITE" if overwritten else "OK"
            page_label = f" page{page_idx}" if page_idx > 0 else ""
            thumb_label = " [thumbnail]" if is_thumbnail else ""
            logger.info(f"[{n}/{total}] {label}{page_label}{thumb_label} | {tiff_path.name} -> {final_path}")
            return ((str(tiff_path), page_idx), status, str(final_path), tiff_path)

        except Exception as e:
            # A read prefetched for this file and never asked for (a reader
            # raised in between) must not wait in this thread for the next
            # file: drop it here too (review V6).
            _clear_exiftool_prefetch()
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


def _thumbnail_sync_guard(tiff_path: Path, output_dir: Path, real_pages: list,
                          thumb_pages: list, page_info: dict):
    """None when this run (re)writes the TIFF's main JXL — "auto"/"scans" then
    decide freely — else whether the ARCHIVED layout has the thumbnail pages.

    The group id hashes the page set (bug #303), so adding a thumbnail to an
    archive whose other pages sync skips would write that one page under a
    NEW id beside the old group: the decoder then sees a complete group plus
    a truncated one. Following what is on disk keeps a sync run from ever
    splitting a group, whichever mode wrote the archive.
    """
    if OVERWRITE is True:
        return None
    with_t = _planned_page_names(tiff_path.stem, real_pages, thumb_pages,
                                 "split", "include")
    without = _planned_page_names(tiff_path.stem, real_pages, thumb_pages,
                                  "split", "exclude")
    # The main output's NAME can differ between the two layouts (a TIFF whose
    # only real page sits after its thumbnail is photo.jxl alone, photo_page1.jxl
    # beside photo_thumbnail.jxl), so either one marks an existing archive.
    mains = {n[0] for n in (with_t, without) if n}
    try:
        existing = [output_dir / m for m in mains if (output_dir / m).exists()]
        if not existing:
            return None
        if OVERWRITE == "smart":
            src_mtime = tiff_path.stat().st_mtime
            # A main output proved to be the same bytes is skipped by the
            # sync, so it does not make this run rewrite the group. This runs
            # while the pages are analysed -- before _mark_sync_same_bytes
            # has seen the plan -- so the proof is made here, for the TIFFs
            # that are newer than an existing main output (rare).
            if any(src_mtime > m.stat().st_mtime
                   and not _prove_same_bytes(tiff_path, m) for m in existing):
                return None
    except OSError:
        return None
    thumb_names = [n for n in with_t if n not in without]
    return bool(thumb_names) and all((output_dir / n).exists() for n in thumb_names)


def _read_page_markers_batch(paths: list) -> dict:
    """{path: {'src': id|None, 'group': id|None, 'thumb': bool, 'pages': int|None}}
    from each file's dc:Relation, in as few exiftool calls as possible. A file
    whose markers cannot be read comes back all None/False: the callers
    (_clean_stale_split_pages, the moved-split adoption) then leave it alone.
    'pages' is the split's jxlphoto-pages count (-1 when unparsable)."""
    markers = {str(p): {"src": None, "group": None, "thumb": False, "pages": None}
               for p in paths}
    index = {os.path.normcase(str(p)): str(p) for p in paths}

    def on_data(chunk, r, data):
        for entry in data:
            src = entry.get("SourceFile")
            rel = entry.get("Relation")
            if src is None or rel is None:
                continue
            info = {"src": None, "group": None, "thumb": False, "pages": None}
            for token in (rel if isinstance(rel, list) else [str(rel)]):
                token = str(token).strip()
                if token.startswith(SRC_PREFIX):
                    info["src"] = token[len(SRC_PREFIX):]
                elif token.startswith(MULTIPAGE_XMP_MARKER):
                    info["group"] = token[len(MULTIPAGE_XMP_MARKER):]
                elif token.startswith(PAGES_XMP_PREFIX):
                    try:
                        info["pages"] = int(token[len(PAGES_XMP_PREFIX):])
                    except ValueError:
                        info["pages"] = -1
                elif token == THUMB_XMP_FLAG:
                    info["thumb"] = True
            key = os.path.normcase(str(Path(src)))
            if key in index:
                markers[index[key]] = info

    def on_error(chunk, e):
        logger.debug(f"Stale split pages: marker batch failed ({e})")

    _exiftool_batch(paths, _RELATION_BATCH_HEAD, "Page markers", on_data, on_error=on_error)
    return markers


def _clean_stale_split_pages(all_items, results, dry_run: bool = False) -> dict:
    """Pages an EARLIER encode of a TIFF left beside the archive this run
    rewrote: the group id hashes the page set (#303), so when a TIFF is
    re-encoded with FEWER pages — a thumbnail page dropped by
    --thumbnail-mode exclude or by "auto" for a re-export from Capture One,
    a scan saved again without its IR page — the old pages stay in the folder
    under the old id. The decoder then warns about an incomplete group and
    writes an extra TIFF, and the recompressor makes derivatives of them.

    Only for TIFFs this run wrote COMPLETELY (every planned page ok) and that
    still exist. A leftover is a file named like one of its split pages (the
    page names of _page_output_name, THUMBNAIL_SUFFIX included) that is not in
    the new layout, carries this TIFF's jxlphoto-src and a group id the new
    pages do not have. A leftover THUMBNAIL page is removed: it is a
    reduced-resolution copy of a TIFF that is still here, with its new archive
    verified. A leftover REAL page is never deleted, only reported.
    Returns {"removed": n, "kept": n}."""
    stats = {"removed": 0, "kept": 0}
    planned, folder_of, tiff_of = {}, {}, {}
    for item in all_items:
        key = os.path.normcase(str(item[0]))
        planned.setdefault(key, set()).add(os.path.normcase(str(item[1])))
        folder_of.setdefault(key, Path(item[1]).parent)
        tiff_of.setdefault(key, Path(item[0]))
    written = {}
    for r in results:
        if r[1] in ("ok", "overwrite"):
            written.setdefault(os.path.normcase(str(r[0][0])), set()).add(
                os.path.normcase(str(r[2])))
    owner = {}          # candidate path -> tiff key
    new_pages = {}      # final path written this run -> tiff key
    listed = {}         # folder -> its entries, read once per folder
    for key, finals in planned.items():
        if written.get(key) != finals or not tiff_of[key].exists():
            continue
        pat = re.compile(r"^" + re.escape(tiff_of[key].stem) + r"(?:_page\d+)?(?:"
                         + re.escape(THUMBNAIL_SUFFIX) + r")?\.jxl$", re.IGNORECASE)
        folder = folder_of[key]
        if folder not in listed:
            try:
                listed[folder] = list(os.scandir(folder))
            except OSError:
                listed[folder] = []
        entries = listed[folder]
        found = [e.path for e in entries
                 if pat.match(e.name) and os.path.normcase(e.path) not in finals
                 and e.is_file(follow_symlinks=False)]
        for p in found:
            owner[p] = key
        if found:
            new_pages.update({p: key for p in finals})
    if not owner:
        return stats
    # Each candidate as it was before its marker was read: one rewritten in
    # between (another run writing a new layout) is not the file judged (#559).
    seen = {}
    for p in owner:
        try:
            seen[p] = os.stat(p)
        except OSError:
            pass
    marks = _read_page_markers_batch(list(owner) + list(new_pages))
    new_groups = {}     # tiff key -> group ids its new pages carry
    for p, key in new_pages.items():
        g = (marks.get(p) or {}).get("group")
        if g:
            new_groups.setdefault(key, set()).add(g)
    for cand, key in sorted(owner.items()):
        m = marks.get(cand) or {}
        if (m.get("src") != _source_path_id(tiff_of[key]) or not m.get("group")
                or m["group"] in new_groups.get(key, set())):
            continue
        if m.get("thumb"):
            if dry_run:
                logger.info(f"Would remove a thumbnail page left by an earlier encode of "
                            f"{tiff_of[key].name}: {cand}")
                stats["removed"] += 1
                continue
            if cand not in seen or _changed_since(cand, seen[cand]):
                stats["kept"] += 1
                logger.warning(f"A thumbnail page left by an earlier encode of "
                               f"{tiff_of[key].name} changed while it was checked "
                               f"(another run?): {cand} — KEPT")
                continue
            try:
                os.unlink(cand)
            except OSError as e:
                logger.warning(f"Could not remove a thumbnail page left by an earlier "
                               f"encode of {tiff_of[key].name}: {cand} ({e})")
                continue
            stats["removed"] += 1
            logger.info(f"Removed a thumbnail page left by an earlier encode of "
                        f"{tiff_of[key].name} (this run writes no such page): {cand}")
        else:
            stats["kept"] += 1
            logger.warning(f"A page of an earlier encode of {tiff_of[key].name} is still "
                           f"beside its new archive, which no longer has it (the TIFF has "
                           f"fewer pages now): {cand} — KEPT; check it and delete it if the "
                           f"TIFF lost that page on purpose")
    return stats


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
        samples, subfiletype = 3, 0
        try:
            with tifffile.TiffFile(str(tiff_path)) as tif:
                if len(tif.pages) == 0:
                    # A header that opens but yields no pages is a broken file,
                    # not an RGB one: classify it exactly as split/split_all do
                    # below (corrupt; does not change the exit code) instead of
                    # falling through with samples=3 and dying in convert_one on
                    # "Page index 0 out of range" — a per-file ERROR, exit 1.
                    raise UnreadableTiff("no readable pages (corrupt or truncated TIFF)")
                _note_subifd_images(tif, tiff_path)
                _note_unarchivable_content(tif, tiff_path)
                samples = int(tif.pages[0].samplesperpixel) if tif.pages[0].samplesperpixel else 1
                # Same reason as samples: ignore encodes page 0 as a real page,
                # so page 0's own SubfileType must travel with it or the decoder
                # writes 0 back — a single-page MASK TIFF lost its role in
                # exactly this branch (the split/skip/split_all planners below
                # read the real value from _analyze_tiff_pages).
                subfiletype = (int(tif.pages[0].subfiletype)
                               if tif.pages[0].subfiletype else 0)
                try:
                    _px = (int(tif.pages[0].imagewidth or 0)
                           * int(tif.pages[0].imagelength or 0))
                except (TypeError, ValueError):
                    _px = 0
                with _PAGE_PIXELS_LOCK:
                    _PAGE_PIXELS[(os.path.normcase(str(tiff_path)), 0)] = _px
                    _PAGE_CHANNELS[(os.path.normcase(str(tiff_path)), 0)] = samples
                # Counting the IFD chain only follows offsets (no pixel decode),
                # and this is the ONLY place that can tell the user pages are
                # being dropped: "ignore" encodes page 0 and discards the rest,
                # which used to happen without a single line of output.
                extra_pages = max(0, len(tif.pages) - 1)
                # Film scan or not (SCAN_DISTANCE): header fields only.
                _facts = {i: _page_facts(i, pg) for i, pg in enumerate(tif.pages)}
                _record_scan(tiff_path, _scan_kind(_facts), _scan_reason(_facts),
                             (_facts.get(0) or {}).get('bits'))
        except UnreadableTiff:
            raise
        except Exception:
            # If we cannot read the page, let convert_one report the error later
            # and fall back to RGB to avoid a planning-time crash.
            samples = 3
            subfiletype = 0
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
        return [(tiff_path, final_jxl, 0, False, subfiletype, samples)]

    real_pages, thumb_pages, page_info = _analyze_tiff_pages(tiff_path)
    _record_scan(tiff_path, _scan_kind(page_info), _scan_reason(page_info),
                 ((page_info or {}).get(0) or {}).get('bits'))

    pages_to_encode = []

    if mp_mode == "skip":
        if not real_pages and not thumb_pages:
            # Same classification as split below: a TIFF with no pages at all
            # is a broken file, not a policy decision. Falling through to
            # idx=0 errored later in convert_one ("Page index 0 out of range")
            # — a per-file ERROR, exit 1 — for a file that is merely corrupt
            # (split documents this as not changing the exit code).
            raise UnreadableTiff("no readable pages (corrupt or truncated TIFF)")
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
        # Every thumbnail page except the one actually being encoded is being
        # dropped here. `skip` is about MULTI-page files, so a 1-real-page TIFF
        # sails through it — but its embedded preview does not, and nothing
        # used to say so. That is the shape of every Capture One export and of
        # the film scans, so on a real library this was silent on every file.
        _record_dropped_thumbnails(tiff_path, len([i for i in thumb_pages if i != idx]))

    elif mp_mode == "split":
        for idx in real_pages:
            info = page_info.get(idx, {'subfiletype': 0, 'samples': 3})
            pages_to_encode.append((idx, False, info['subfiletype'], info['samples']))
        kept = _kept_thumbnail_pages(thumb_pages, THUMBNAIL_MODE, page_info)
        archived = None
        if kept and THUMBNAIL_MODE.lower() in ("auto", "scans"):
            archived = _thumbnail_sync_guard(tiff_path, output_dir, real_pages,
                                             kept, page_info)
            if archived is False:
                kept = []
        for idx in kept:
            info = page_info.get(idx, {'subfiletype': 1, 'samples': 3})
            pages_to_encode.append((idx, True, info['subfiletype'], info['samples']))
        if len(kept) < len(thumb_pages):
            # Dropping thumbnails here is what --thumbnail-mode ASKED for, so
            # it is reported once in the summary rather than once per file
            # (--warn-thumbnail-discard brings the per-file lines back).
            # Counted apart from _multipage_ignored so the summary can name the
            # right setting.
            _record_dropped_thumbnails(tiff_path, len(thumb_pages) - len(kept),
                                       archived=archived is False)
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

# Per-file lines are capped like the discard warnings: a library re-archived
# after a structure change could otherwise print one line per photo.
STALE_WARN_LIMIT = 20


def _warn_stale_split_outputs(tasks: list) -> list:
    """Name outputs of an EARLIER split still sitting in this run's destination.

    A split's output names come from the page indices of the source, so a
    source whose page structure changed — the usual cause being a decode that
    dropped an excluded thumbnail — writes a DIFFERENT set of names, and the
    previous set stays on disk. Those leftovers are what the decoder has to
    disentangle later (see _split_group_by_srcsum there), and until v2.0.2
    could not.

    Nothing is deleted here: removing an output is a different kind of decision
    from writing one, and a `<stem>_page1.jxl` can legitimately belong to a
    source of its own. Saying so at archive time is what lets the user act
    before the folder is ever decoded.

    Returns the leftovers found, so tests can assert on them.
    """
    # Only sources this run SPLITS can leave a page trail behind.
    planned_by_dir: Dict[Path, set] = {}
    stems_by_dir: Dict[Path, set] = {}
    for tiff, _w, final_jxl, _p, _th, _sf, _sp, group_id, _gt in tasks:
        d = final_jxl.parent
        planned_by_dir.setdefault(d, set()).add(os.path.normcase(final_jxl.name))
        if group_id:
            # Normcased: the comparison below runs against normcased directory
            # names, so a leftover scan_page2.jxl next to a re-split Scan.tif
            # would otherwise never match on Windows.
            stems_by_dir.setdefault(d, set()).add(os.path.normcase(tiff.stem))

    stale = []
    for d, stems in stems_by_dir.items():
        planned = planned_by_dir.get(d, set())
        try:
            present = {os.path.normcase(f.name): f for f in d.iterdir()
                       if f.suffix.lower() == ".jxl"}
        except OSError:
            continue        # destination not readable/created yet: nothing to say
        for name_lc, f in sorted(present.items()):
            if name_lc in planned:
                continue
            stem, page_idx, is_thumb = _parse_output_page_suffix(f.stem)
            if os.path.normcase(stem) not in stems or (page_idx == 0 and not is_thumb):
                continue    # not a page of a stem this run splits
            stale.append(f)

    if not stale:
        return stale
    logger.warning(
        f"{len(stale)} JXL(s) in the destination look like pages of a PREVIOUS split "
        f"of the same source and are not part of this run's output. A leftover "
        f"THUMBNAIL page of a source this run rewrites completely is removed at the "
        f"end, once its marker proves it came from that source; anything else here "
        f"is kept — check it and remove it, or the decoder will have to tell it "
        f"apart from the real split:")
    for f in stale[:STALE_WARN_LIMIT]:
        logger.warning(f"    {f}")
    if len(stale) > STALE_WARN_LIMIT:
        logger.warning(f"    ... and {len(stale) - STALE_WARN_LIMIT} more")
    return stale


def _parse_output_page_suffix(stem: str):
    """(source stem, page index, is_thumbnail) for a name this encoder writes.

    Mirrors _page_output_name. The decoder has its own copy
    (_parse_jxl_page_suffix) reading the same convention from the other side;
    they are deliberately separate so neither script has to import the other.
    """
    is_thumb = False
    if THUMBNAIL_SUFFIX and stem.endswith(THUMBNAIL_SUFFIX):
        is_thumb = True
        stem = stem[:-len(THUMBNAIL_SUFFIX)]
    page_idx = 0
    m = re.search(r'_page(\d+)$', stem)
    if m:
        page_idx = int(m.group(1))
        stem = stem[:m.start()]
    return stem, page_idx, is_thumb


def _legacy_group_id(tiff_path: Path) -> str:
    """The pre-v2.0.2 group id: a hash of the source PATH alone.

    Bug #303 replaced this with a hash of path + produced page set. Archives
    split by v2.0.0/v2.0.1 still carry this id on every page, which is what
    _adopt_legacy_group_ids looks for. Must hash EXACTLY what the old code
    hashed or no existing archive will ever match.
    """
    return hashlib.sha256(str(tiff_path.resolve()).encode("utf-8")).hexdigest()[:16]


def _read_group_markers_batch(outputs: list) -> dict:
    """{output path: group id|None} in as few exiftool calls as possible.

    Mirrors _read_source_markers_batch but extracts only the multi-page marker;
    kept separate because that reader's result shape is part of the provenance
    layer's contract. A file whose marker cannot be read comes back None, which
    the caller treats as "cannot prove anything": the new id formula stands.
    """
    markers = {str(o): None for o in outputs}
    index = {os.path.normcase(str(o)): str(o) for o in outputs}

    def on_data(chunk, r, data):
        for entry in data:
            src = entry.get("SourceFile")
            rel = entry.get("Relation")
            if src is None or rel is None:
                continue
            values = rel if isinstance(rel, list) else [str(rel)]
            gid = None
            for token in values:
                token = str(token).strip()
                if token.startswith(MULTIPAGE_XMP_MARKER):
                    gid = token[len(MULTIPAGE_XMP_MARKER):]
            key = os.path.normcase(str(Path(src)))
            if key in index:
                markers[index[key]] = gid

    def on_error(chunk, e):
        # Never fail an archive run over an optimization: unreadable
        # markers simply mean the new formula is used.
        logger.debug(f"Legacy group-id check: marker batch failed ({e})")

    _exiftool_batch(outputs, _RELATION_BATCH_HEAD, "Group markers", on_data, on_error=on_error)
    return markers


def _split_group_id(key: str, pages) -> str:
    """The group id of a split: its resolved source path + the page set it
    produces (bug #303; see process_group's _make_group_id)."""
    pages = sorted(pages)
    blob = key + "|" + ",".join(f"{p}{'t' if t else ''}" for p, t in pages)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _adopt_legacy_group_ids(split_tiffs: Dict[str, Path],
                            dir_per_tiff: Dict[str, Path],
                            pages_per_tiff: Dict[str, set]) -> Dict[str, str]:
    """{resolved tiff key: legacy group id to stamp} for splits that should heal.

    Bug #303 changed the group id formula from the path alone to path + page
    set, unconditionally. So when one page output of a v2.0.0/v2.0.1 archive is
    lost and re-encoded — the surviving pages skipped by sync/no-overwrite but
    still planned — the re-encoded page would get the NEW id while its siblings
    keep the legacy one, and the decoder sees two truncated groups where every
    page is in fact present.

    The fix is to look before writing: if the destination already holds sibling
    pages of this source (same stem/page naming) that UNANIMOUSLY carry this
    source's legacy id, stamp this run's pages with that same id so the archive
    heals back into one group. Unanimity plus an exact match against the legacy
    formula is what makes this safe — mixed ids, missing markers or same-stem
    files of a DIFFERENT source (its own name ends in _page<N>) all fail the
    check and keep the new formula. Costs one batched exiftool call per run,
    only when a split's destination holds candidate siblings.
    """
    adopted: Dict[str, str] = {}
    candidates: Dict[str, list] = {}
    for key, tiff in split_tiffs.items():
        d = dir_per_tiff[key]
        try:
            present = [f for f in d.iterdir() if f.suffix.lower() == ".jxl"]
        except OSError:
            continue        # destination not readable/created yet: nothing to adopt
        sibs = [f for f in present
                if os.path.normcase(_parse_output_page_suffix(f.stem)[0])
                == os.path.normcase(tiff.stem)]
        if sibs:
            candidates[key] = sibs
    if not candidates:
        return adopted
    marks = _read_group_markers_batch(
        [f for sibs in candidates.values() for f in sibs])
    moved: Dict[str, tuple] = {}
    for key, sibs in candidates.items():
        # Only heal when every on-disk page is part of THIS run's page set. A
        # page the run no longer produces is a leftover of a changed structure
        # (bug #303's own shape), and adopting the legacy id there would invite
        # it back into the group.
        planned = pages_per_tiff.get(key, set())
        if not all(_parse_output_page_suffix(f.stem)[1:] in
                   {(p, t) for p, t in planned} for f in sibs):
            continue
        ids = {marks.get(str(f)) for f in sibs}
        legacy = _legacy_group_id(split_tiffs[key])
        if ids == {legacy}:
            adopted[key] = legacy
            logger.info(
                f"Multi-page archive predates the v2.0.2 group-id change: the "
                f"{len(sibs)} existing page(s) of {split_tiffs[key].name} all carry "
                f"its legacy group id, so this run's page(s) are stamped with the "
                f"SAME id and the archive heals into one group.")
        elif (len(ids) == 1 and None not in ids
              and ids != {_split_group_id(key, planned)}):
            moved[key] = (next(iter(ids)), sibs)
    # #576: the id hashes the source's absolute PATH, so a split whose folder
    # moved (another drive letter, a renamed event folder) and lost one page
    # would get a new id on that page while its siblings keep the old one —
    # two incomplete groups. Adopt the siblings' id when they unanimously
    # carry it AND their jxlphoto-srcsum proves they were made from this
    # TIFF's current bytes (an unreadable marker never adopts). When the id
    # is already the one this run computes, adopting it changes nothing.
    if moved:
        _moved_files = [f for _gid, sibs in moved.values() for f in sibs]
        src_marks = _read_source_markers_batch(_moved_files)
        # The split must also be the SAME page set (261104 review, S1): an
        # unchanged TIFF whose planned pages changed (a thumbnail now kept)
        # must not lend the old group's id to the new page — the pages would
        # then declare different counts and the decoder refuses the group.
        page_marks = _read_page_markers_batch(_moved_files)
        for key, (gid, sibs) in moved.items():
            infos = [src_marks.get(str(f)) or {} for f in sibs]
            if any(i.get("unread") or not i.get("srcsum") for i in infos):
                continue
            pages = [page_marks.get(str(f)) or {} for f in sibs]
            if any(p.get("group") != gid for p in pages):
                continue            # the page read failed or disagrees
            n_planned = len(pages_per_tiff.get(key, ()))
            if any(p.get("pages") is not None and p.get("pages") != n_planned
                   for p in pages):
                continue
            try:
                content = _file_content_id(split_tiffs[key])
            except OSError:
                continue
            if any(i["srcsum"] != content for i in infos):
                continue
            adopted[key] = gid
            logger.info(
                f"Multi-page archive moved or renamed since it was split: the "
                f"{len(sibs)} existing page(s) of {split_tiffs[key].name} carry "
                f"group id {gid} and this TIFF's content, so this run's page(s) "
                f"are stamped with the SAME id.")
    return adopted


@_holds_staging
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
    pages_per_tiff: Dict[str, set] = {}
    for tiff, _final_jxl, _page_idx, _is_thumbnail, _subfiletype, _samples in group_items:
        _k = str(tiff.resolve())
        outputs_per_tiff[_k] = outputs_per_tiff.get(_k, 0) + 1
        pages_per_tiff.setdefault(_k, set()).add((_page_idx, bool(_is_thumbnail)))

    def _make_group_id(tiff_path: Path) -> str:
        """Identify THIS split, not just its source.

        The path alone made the id survive a re-encode, and that is what let
        outputs of two different splits share one group. A scan of
        [real, thumbnail, IR] archives as pages {0, 2} under
        --thumbnail-mode exclude; decode it and the reconstructed TIFF is
        [real, IR], so re-encoding the same file in the same folder writes
        pages {0, 1} and leaves `_page2.jxl` behind carrying the SAME id. The
        decoder then pulled all three into one group and wrote a TIFF with a
        duplicated page (bug #303).

        Hashing the set of pages the split actually produces fixes that at the
        source: two runs whose output NAMES differ get different ids, so a
        leftover can never join the new group, while re-encoding a file whose
        structure did not change keeps the id stable (the outputs simply
        overwrite each other, which is what a sync run should do). It costs no
        I/O — the page list is already in hand.
        """
        key = str(tiff_path.resolve())
        return _split_group_id(key, pages_per_tiff.get(key, ()))

    # The formula above is unconditional, which strands archives split by
    # v2.0.0/v2.0.1: they carry the LEGACY id, and re-encoding one lost page of
    # such an archive would stamp it with the new id while its skipped siblings
    # keep the old one — two truncated groups, every page present. Adopt the
    # legacy id where the existing siblings provably carry it.
    split_tiffs: Dict[str, Path] = {}
    dir_per_tiff: Dict[str, Path] = {}
    for tiff, final_jxl, _page_idx, _is_thumbnail, _subfiletype, _samples in group_items:
        _k = str(tiff.resolve())
        if outputs_per_tiff.get(_k, 0) > 1:
            split_tiffs[_k] = tiff
            dir_per_tiff[_k] = final_jxl.parent
    legacy_ids = _adopt_legacy_group_ids(split_tiffs, dir_per_tiff, pages_per_tiff)

    tasks = []
    for tiff, final_jxl, page_idx, is_thumbnail, subfiletype, samples in group_items:
        if use_staging:
            # Unique staging name to avoid collisions across different source folders/pages
            write_jxl = staging_dir / f"{uuid.uuid4().hex}_{tiff.stem}_p{page_idx}.jxl"
        else:
            write_jxl = final_jxl
        tiff_key = str(tiff.resolve())
        _n_out = outputs_per_tiff.get(tiff_key, 0)
        group_id = None
        if _n_out > 1:
            group_id = legacy_ids.get(tiff_key) or _make_group_id(tiff)
        # Every page of a TIFF resolves to the SAME output folder, and this
        # group holds one folder, so _n_out is the whole split — not a slice.
        group_total = _n_out if group_id else None
        tasks.append((tiff, write_jxl, final_jxl, page_idx, is_thumbnail, subfiletype,
                      samples, group_id, group_total))

    _warn_stale_split_outputs(tasks)

    moved_finals = set()
    refused_moves = {}      # (str(tiff), page_idx) -> why (#615)
    failed_moves = set()    # (str(tiff), page_idx) (#624)

    def _move_dest_from_staging(dest_tasks: list, status_map: Dict):
        """Bulk-move one destination folder's outputs out of staging."""
        moved = 0
        for tiff, write_jxl, final_jxl, page_idx, _, _, _, _, _ in dest_tasks:
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
            # A locked/readonly destination must not abort the whole batch:
            # the file stays in staging and is logged for manual recovery.
            _why = _output_changed_since_planned(final_jxl)
            if _why:
                _swap_refusals.append(str(final_jxl))
                refused_moves[(str(tiff), page_idx)] = _why
                logger.error(f"  REFUSED, kept in staging | {write_jxl.name} -> "
                             f"{final_jxl} | {_why}")
                continue
            if _promote_from_staging(write_jxl, final_jxl):
                moved += 1
                moved_finals.add(os.path.normcase(str(final_jxl)))
            else:
                failed_moves.add((str(tiff), page_idx))
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
    if tasks:
        # Film scans can run at another distance (SCAN_DISTANCE): budget for
        # the hungrier of the distances this run encodes at.
        _budget_d = max({_distance_for(t[0]) for t in tasks},
                        key=lambda _d: _cjxl_bytes_per_pixel(
                            _d, CJXL_EFFORT, CJXL_BUFFERING, modular=bool(CJXL_MODULAR)))
        workers = _memory_capped_workers(
            workers,
            max(_PAGE_PIXELS.get((os.path.normcase(str(t[0])), t[3]), 0)
                for t in tasks),
            _budget_d, CJXL_EFFORT, CJXL_BUFFERING,
            modular=bool(CJXL_MODULAR),
            extra_bytes_per_pixel=_python_bytes_per_pixel(max(
                _PAGE_CHANNELS.get((os.path.normcase(str(t[0])), t[3]), 3)
                for t in tasks)))
    for _t in tasks:
        _record_source_identity(_t[0])      # X2: before anything reads it
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(convert_one, t, w, f, p, th, sft, spl, g, gt):
                   (t, w, f, p, th, sft, spl, g, gt)
                   for t, w, f, p, th, sft, spl, g, gt in tasks}
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

    if refused_moves:
        # An error like any other (#615): counted in the summary and the
        # failures list, never "written" for the stale-page cleanup, and the
        # delete gate below keeps the TIFF.
        results = [(r[0], "error",
                    f"REFUSED to write {Path(r[2]).name}: {refused_moves[r[0]]} — "
                    f"its output is kept in staging and the source is kept", None)
                   if r[0] in refused_moves else r for r in results]
    if failed_moves:
        # #624: an output that did not reach its folder is an error, not "ok".
        results = [(r[0], "error",
                    f"MOVE FAILED: {Path(r[2]).name} did not reach its folder (see "
                    f"MOVE FAILED above) — the file under that name is the OLD one, "
                    f"and the source is kept", None)
                   if r[0] in failed_moves else r for r in results]

    # Delete source TIFFs after confirmed encode, in ANY mode, only after staging.
    # A source TIFF is deleted only if ALL of its encoded pages succeeded and every
    # resulting JXL exists at its final destination.
    #
    # Mode 8 used to be the only mode allowed here, which made "archive to
    # another folder and drop the master" impossible. Nothing in the gates
    # below was ever mode-specific: they all certify THIS run's output at its
    # FINAL path, which for modes 1-7 is the destination folder. What keeps the
    # collapsing modes safe (5 merges sibling folders, 6/7 drop one level under
    # the marker, 2 flattens a whole tree) is _abort_on_duplicate_outputs, which
    # runs in every mode before the first byte is written — two sources mapping
    # to one output abort the run instead of costing two originals.
    if DELETE_SOURCE:
        _delete_sources(results, tasks, moved_finals, use_staging)

    return results


def _delete_sources(results, tasks, moved_finals, use_staging):
    """The delete gate of process_group, moved here unchanged (refactor plan
    261113, A2): each source TIFF of these results is deleted only when every
    check below proves it archived, in the same order as before."""
    deleted = 0
    deleted_skipped = 0
    # Group results by source TIFF path
    results_by_tiff: Dict[str, list] = {}
    for result in results:
        key = result[0][0]  # str(tiff_path)
        results_by_tiff.setdefault(key, []).append(result)

    # Index tasks by final path ONCE (the old nested loop was O(n^2)
    # and rebuilt the result set for every source TIFF).
    by_final = {}
    for _, _, final_jxl, _, _, _, _, _, _ in tasks:
        by_final[str(final_jxl)] = final_jxl

    # A SKIPPED page is deleted on the strength of a PRE-EXISTING output
    # this run never wrote — its own status proves nothing but "a file by
    # this name exists". Before the checks below such an output used to be
    # certified only by exists() + integrity (+ an optional pixel
    # round-trip), and a same-named JXL of ANOTHER photo (a camera's name
    # counter resetting across cards, or simply OVERWRITE=False's "SKIP
    # (exists)" on any age) deleted the master TIFF for nothing. One
    # batched exiftool pass over all skipped finals + sources proves the
    # pairing (`_skipped_archive_proof`); an unreadable, absent or
    # foreign marker fails CLOSED (the source is kept). The dry-run
    # preview of --delete-skipped runs the same predicate.
    _skip_proof = {}
    if DELETE_SKIPPED:
        _skip_pairs = []
        for _tk, _trs in results_by_tiff.items():
            for _r in _trs:
                if _r[1] == "skipped":
                    _skip_pairs.append((Path(_tk), Path(_r[2])))
        if _skip_pairs:
            _skip_proof = _skipped_archive_proof(_skip_pairs)

    for tiff_key, tiff_results in results_by_tiff.items():
        # Every page must have freshly succeeded — or, with DELETE_SKIPPED,
        # have been skipped because its output was already there. A skip
        # carries NO proof of its own (see the DELETE_SKIPPED note): it is
        # admitted here only so the checks below can judge it on the file
        # itself, never on the timestamp that produced the skip.
        def _page_settled(r) -> bool:
            if r[1] in ("ok", "overwrite") and r[3] is not None:
                return True
            return DELETE_SKIPPED and r[1] == "skipped"

        all_ok = all(_page_settled(r) for r in tiff_results)
        if not all_ok:
            _blocked_by_skip = (not DELETE_SKIPPED
                                and any(r[1] == "skipped" for r in tiff_results))
            _hint = (" | its output already exists; pass --delete-skipped to "
                     "delete sources that are already archived"
                     if _blocked_by_skip else "")
            _delete_stats["kept"] += 1
            logger.warning(
                f"  KEEP source (not all pages succeeded) | {Path(tiff_key).name}{_hint}")
            continue

        # Which of this TIFF's outputs were skipped rather than written.
        # Needed twice below: the staging gate cannot apply to them (nothing
        # was staged, correctly), and the log line names them differently.
        skipped_finals = {os.path.normcase(r[2]) for r in tiff_results
                          if r[1] == "skipped"}

        # Pages this run chose NOT to encode. Every downstream check passes
        # in this case — the JXL that was written is valid and complete —
        # so this is the only place that can stop the deletion. Fail closed:
        # a page that exists only in the source must keep the source alive.
        if os.path.normcase(tiff_key) in _discarded_real_page_sources:
            _delete_stats["kept"] += 1
            logger.warning(
                f"  KEEP source (pages were discarded, deleting would lose them) | "
                f"{Path(tiff_key).name} | re-run with --multipage-mode split to encode "
                f"every page, then delete")
            continue
        _held = _unarchived_content_sources.get(os.path.normcase(tiff_key))
        if _held:
            _delete_stats["kept"] += 1
            logger.warning(
                f"  KEEP source ({_held}, which its JXL does not keep as they "
                f"are — deleting would lose them) | {Path(tiff_key).name}")
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
            #
            # A SKIPPED page is the one legitimate exception: nothing was
            # written, so nothing was staged, so it can never appear in
            # moved_finals. Without this the flag would look like it worked
            # and silently delete nothing whenever staging is configured.
            # It is not a hole — such a page is judged entirely by the
            # exists()/integrity/round-trip checks around this one.
            if (use_staging
                    and os.path.normcase(str(final_jxl)) not in moved_finals
                    and os.path.normcase(str(final_jxl)) not in skipped_finals):
                can_delete = False
                break
            if not _verify_jxl_integrity(Path(final_jxl)):
                can_delete = False
                break

        # For the skipped pages of this source: the pre-existing output
        # must PROVE it is an archive of THIS photo (E-1, above). Runs
        # after the integrity loop so a broken file is named as such; a
        # mixed source (one page freshly written, one skipped) is judged
        # page by page exactly like an all-skipped one.
        _prov_reject = None
        if can_delete and skipped_finals and DELETE_SKIPPED:
            for r in tiff_results:
                if r[1] != "skipped":
                    continue
                if _skip_proof.get((os.path.normcase(tiff_key),
                                    os.path.normcase(r[2])), False):
                    continue
                can_delete = False
                _prov_reject = ("its existing output failed the provenance "
                                "check — the markers do not prove that file "
                                "is an archive of THIS photo as it is now "
                                "(absent, unreadable, foreign, or the TIFF "
                                "changed since that output was written); "
                                "re-encode it with --overwrite, or run "
                                "--provenance adopt with a "
                                "folder-collapsing mode")
                break

        if not can_delete:
            _delete_stats["kept"] += 1
            if _prov_reject:
                logger.warning(
                    f"  KEEP source (provenance check failed) | "
                    f"{Path(tiff_key).name} | {_prov_reject}")
            else:
                logger.warning(f"  KEEP source (JXL integrity check failed) | {Path(tiff_key).name}")
            continue

        # Last gate, and the only one that looks at PIXELS. Everything above
        # certifies the FILE; this certifies the IMAGE. Runs per page, so a
        # split multi-page TIFF only dies once every one of its pages came
        # back. Failure is a hard KEEP: the whole point is that the source
        # is about to stop existing.
        if VERIFY_ROUNDTRIP:
            verify_failed = None
            for r in tiff_results:
                _page = r[0][1]
                ok_v, detail = _verify_roundtrip_page(
                    Path(tiff_key), _page, Path(r[2]), _distance_for(tiff_key))
                if not ok_v:
                    verify_failed = (_page, detail)
                    break
                logger.debug(f"  verify page{_page} OK ({detail}) | {Path(tiff_key).name}")
            if verify_failed is not None:
                _page, detail = verify_failed
                _delete_stats["kept"] += 1
                # An output this run did NOT write is the likeliest thing to
                # fail here, and for a reason that has nothing to do with a
                # bad encode: a pre-existing archive made at a different
                # --distance is judged against THIS run's thresholds. Say so
                # rather than let it read as corruption.
                _pre = (" | this output was not written by this run (SKIP); "
                        "if it was archived at a different --distance, re-run "
                        "with that distance or set VERIFY_ROUNDTRIP = False"
                        + _scan_distance_hint(tiff_key)
                        if skipped_finals else "")
                logger.warning(
                    f"  KEEP source (round-trip verification FAILED on page {_page}: "
                    f"{detail}) | {Path(tiff_key).name}{_pre}")
                continue

        src_tiff = Path(tiff_key)
        if _source_changed_since_read(src_tiff):
            _delete_stats["kept"] += 1
            logger.warning(f"  KEEP source (source changed since this run read it (re-exported or rewritten during the run?) — its current version was never archived) | {src_tiff.name}")
            continue
        # #617: every page's JXL on the disk, not only in the OS cache,
        # before the irreversible delete.
        if not all(_flush_to_disk(f) for f in sorted({r[2] for r in tiff_results})):
            _delete_stats["kept"] += 1
            logger.warning(f"  KEEP source (output could not be flushed to disk) | "
                           f"{src_tiff.name}")
            continue
        try:
            src_tiff.unlink()
            deleted += 1
            # A dropped thumbnail does not block the delete (it is a reduced
            # copy of a page that IS in the output), but the decoded TIFF
            # will not have that preview page back — say so at delete time.
            thumb_note = (" (embedded thumbnail page not encoded)"
                          if os.path.normcase(tiff_key) in _discarded_thumb_sources else "")
            # Named apart from a fresh delete: this source was cleared by a
            # file THIS RUN DID NOT WRITE, and the log should not blur the
            # two — that distinction is the whole reason the flag is opt-in.
            # "already archived" only when NOTHING was written for this
            # source this run. A mixed source (one page skipped, one freshly
            # converted) is a normal delete with a note, not an archived one.
            _all_skipped = len(skipped_finals) == len({r[2] for r in tiff_results})
            if _all_skipped:
                deleted_skipped += 1
                _delete_stats["deleted_archived"] += 1
                logger.info(f"  DELETED source (already archived) | "
                            f"{src_tiff.name}{thumb_note}")
            else:
                _note = " (partly already archived)" if skipped_finals else ""
                logger.info(f"  DELETED source{_note} | {src_tiff.name}{thumb_note}")
            _delete_stats["deleted"] += 1
        except OSError as e:
            _delete_stats["kept"] += 1
            logger.warning(f"  KEEP (could not delete source) | {src_tiff.name}: {e}")
    if deleted:
        _sk = (f" ({deleted_skipped} already archived)" if deleted_skipped else "")
        logger.info(f"  -> Deleted {deleted} source TIFF(s){_sk}")


# Matched case-insensitively against f.suffix.lower(). Globbing the four
# spellings ("*.tif", "*.TIF", ...) missed mixed case like "Photo.Tif" on
# case-sensitive filesystems (Linux/macOS) — the file was skipped in silence.
TIFF_EXTS = frozenset({".tif", ".tiff"})


_path_excluded_below_root = _common._path_excluded_below_root


def _iter_tiffs(paths, root=None):
    seen = set()
    files = []
    excluded = 0
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
        if _path_excluded_below_root(f, root, EXCLUDE_FOLDERS):
            excluded += 1
            continue
        if key not in seen:
            seen.add(key)
            files.append(f)
    _scan_done(_scan, len(files))
    if excluded > 0:
        logger.info(f"Excluded {excluded} file(s) under: {', '.join(EXCLUDE_FOLDERS)}")
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
#
# The names are the decoder's SETTINGS (EXPORT_TIFF_FOLDER, TIFF_FOLDER_NAME,
# CONVERTED_TIFF_FOLDER): read from the jxl_tiff_decoder.py that sits next to
# this script (E7 of the 2026-10-08 audit — a literal copy of the defaults
# silently stopped protecting a user who renamed them). The shipped defaults
# stay in the set as well: skipping a folder only means its TIFFs are not
# picked up here, while re-encoding a decode is a generational loss.
_DECODER_DEFAULT_OUTPUT_FOLDERS = frozenset({"16b_tiff", "tiff_16bits", "converted_tiff"})
_DECODER_FOLDER_SETTINGS = ("EXPORT_TIFF_FOLDER", "TIFF_FOLDER_NAME",
                            "CONVERTED_TIFF_FOLDER")


def _decoder_output_folders() -> frozenset:
    """The decoder's configured output folder names (lowercased), read from
    its source with `ast` — nothing is imported or executed — plus the
    shipped defaults. A missing or unreadable decoder leaves the defaults."""
    names = set(_DECODER_DEFAULT_OUTPUT_FOLDERS)
    try:
        import ast
        src = (Path(__file__).resolve().parent / "jxl_tiff_decoder.py").read_text(
            encoding="utf-8")
        for node in ast.parse(src).body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in _DECODER_FOLDER_SETTINGS
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    and node.value.value.strip()):
                names.add(node.value.value.strip().lower())
    except Exception:
        pass
    # ...and the decoder's values in the script settings overlay.
    try:
        section = _settings_overlay_section(_settings_overlay_path(),
                                            "jxl_tiff_decoder") or {}
        for name in _DECODER_FOLDER_SETTINGS:
            ok, value = _overlay_value(("name",), section.get(name))
            if ok:
                names.add(value.lower())
    except Exception:
        pass
    return frozenset(names)


_DECODER_OUTPUT_FOLDERS = _decoder_output_folders()


def _skip_decoder_output(parts_below_marker, honor_requested_subfolder: bool = True) -> bool:
    """True if any part below the export marker is a decoder output folder —
    EXCEPT the one explicitly requested via EXPORT_TIFF_SUBFOLDER: an
    explicit user request always wins over this heuristic.

    That exemption is mode-7 semantics. Mode 6 promises ALL TIFFs under the
    marker and has no requested subfolder, so a leftover EXPORT_TIFF_SUBFOLDER
    value must not exempt a decoder folder there (a decoded TIFF re-encoded at
    d>0 is generational loss). Mode 6 callers pass
    honor_requested_subfolder=False.
    """
    requested = EXPORT_TIFF_SUBFOLDER.lower() if honor_requested_subfolder else ""
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
        export_idx = _marker_anchor_index(parts_str, input_path, marker_lower)
        if export_idx is not None:
            # Skip decoder output folders (16B_TIFF etc.) below the marker —
            # unconditionally: mode 6 has no requested subfolder, so the
            # EXPORT_TIFF_SUBFOLDER exemption must not apply here.
            below = [p.lower() for p in parts_str[export_idx + 1:]]
            if _skip_decoder_output(below, honor_requested_subfolder=False):
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
        export_idx = _marker_anchor_index(parts_str, input_path, marker_lower)
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

def build_parser():
    """The command line (--flags only; main() validates and applies them).
    Its own function so the wrapper can check an expert flag against it
    (parse_known_args) before a run, and a test can compare it with the
    documentation."""
    # allow_abbrev=False: a shortened flag (`--delete-so`, `--work 4`) is an
    # error, never read as the full one (v2.30.0, docs/upgrading.md).
    parser = argparse.ArgumentParser(description="Batch TIFF 16-bit -> JPEG XL converter",
                                     allow_abbrev=False)
    parser.add_argument("--version", action="version",
                        version=f"{TOOLKIT_NAME} {TOOLKIT_VERSION}")
    parser.add_argument("input",             type=Path, nargs="?", help="Input root folder")
    parser.add_argument("output", nargs="?", type=Path,
                        help="Output folder (modes 0 and 2; ignored by the others, "
                             "which derive the destination from each source's folder)")
    parser.add_argument("--mode",            type=int, default=0, choices=[0,1,2,3,4,5,6,7,8],
                        help="Output folder mode 0-8 (default 0); 6/7 = below the export "
                             "marker, 8 = next to each TIFF. See docs/README_jxl_tiff_encoder.md")
    parser.add_argument("--workers",         type=int, default=min(os.cpu_count() or 4, 16),
                        help="Parallel workers (default: min(CPU, 16); fewer when memory is short)")
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
                        help="Delete source TIFFs after their JXL is written to the "
                             "mode's destination and verified. Works in EVERY mode "
                             "(0-8), not just 8. IRREVERSIBLE.")
    parser.add_argument("--provenance", type=str, default=None,
                        choices=["path", "content", "adopt"],
                        help="How an EXISTING output is matched to the source about to "
                             "overwrite it. Every run refuses to overwrite an output "
                             "whose marker names a DIFFERENT source; with "
                             "--delete-source in modes 2/4/5/6/7 every existing output "
                             "must also prove it came from this source. "
                             "path (default) compares the recorded source LOCATION: "
                             "free, and survives re-exporting a file in place. content "
                             "also accepts matching source bytes, so it survives MOVED "
                             "folders, at the cost of reading each source. adopt is for "
                             "an archive made BEFORE these markers existed: an output "
                             "with NO marker is verified against the source (the adopt "
                             "scan) and then stamped, so the archive heals and later "
                             "runs are strict again. A MISMATCHING marker always fails "
                             "closed: not converted, nothing overwritten, nothing "
                             "deleted.")
    parser.add_argument("--no-overwrite-guard", action="store_true",
                        help="Allow overwriting an existing output whose provenance "
                             "marker names a DIFFERENT source (a renamed or moved "
                             "folder, or another photo with the same name). Each such "
                             "overwrite is logged. For libraries whose sources are "
                             "never deleted; refused together with --delete-source. "
                             "Same as OVERWRITE_GUARD = False.")
    parser.add_argument("--restamp-moved", action="store_true",
                        help="Only fix the provenance record of masters whose folder "
                             "was renamed or moved: for each existing output whose "
                             "jxlphoto-src names another path but whose jxlphoto-srcsum "
                             "matches this TIFF's bytes, rewrite jxlphoto-src to the new "
                             "path. Nothing is encoded, pixels and file date are kept. "
                             "Outputs whose content no longer matches are listed and left "
                             "alone. Refused with --delete-source, --delete-skipped and "
                             "--overwrite.")
    parser.add_argument("--restamp-then-sync", action="store_true",
                        help="Like --restamp-moved (rewrite jxlphoto-src of masters whose "
                             "folder was renamed or moved, when jxlphoto-srcsum still "
                             "matches the TIFF), then continue as --sync in the same run: "
                             "unchanged photos stay up to date, newer TIFFs are re-encoded. "
                             "Reads the record of every existing master. Refused with "
                             "--delete-source, --delete-skipped, --overwrite and "
                             "--restamp-moved.")
    parser.add_argument("--no-adopt-scan", action="store_true",
                        help="[with --provenance adopt] Skip the verification and TRUST "
                             "the existing pairing. Much faster on a large library, and "
                             "only defensible when you know the archive was never built "
                             "with a folder-collapsing mode that could have put a "
                             "different photo under that name.")
    parser.add_argument("--delete-skipped", action="store_true",
                        help="[with --delete-source] Also delete sources whose output "
                             "ALREADY EXISTS (the files reported as SKIP), so an archive "
                             "interrupted between the encode and the unlink can be "
                             "finished without re-encoding everything. NEVER on the "
                             "timestamp alone: the output must still exist and pass the "
                             "integrity check AND carry this source's provenance "
                             "marker; --verify-roundtrip is still recommended — the "
                             "marker names the source file, it does not compare the "
                             "pixels.")
    parser.add_argument("--verify-roundtrip", action="store_true",
                        help="Before deleting a source, decode its JXL and compare it "
                             "against the source page: pixel-identical for --distance 0, "
                             "or a brightness+PSNR sanity check for lossy (catches a "
                             "black/scrambled encode, not quality loss). Roughly doubles "
                             "the run. Needs djxl and imagecodecs. Only affects "
                             "--delete-source. On by default (VERIFY_ROUNDTRIP = True); "
                             "set it to False at the top of the script to turn it off.")
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
                        choices=["auto", "scans", "exclude", "include"],
                        help="When splitting: auto (default: keep thumbnail pages except "
                             "THUMBNAIL_DROP_SOFTWARE's, e.g. Capture One), scans (keep "
                             "them for SCANNER_SOFTWARE or an IR page only), exclude or include")
    parser.add_argument("--scan-distance", type=str, default=None, metavar="D",
                        help="Distance for film scans (SCANNER_SOFTWARE or an IR page): "
                             "0 = lossless (default, SCAN_DISTANCE), 'same' = the run's "
                             "--distance")
    parser.add_argument("--scan-scope", type=str, default=None, choices=["all", "raw"],
                        help="Which film scans --scan-distance covers: all (default) or "
                             "raw only (scanner profile, no profile at 16 bits, or IR)")
    parser.add_argument("--eight-bit-distance", type=str, default=None, metavar="D",
                        help="Distance for 8-bit TIFFs that are not film scans: 'auto' "
                             "(default, EIGHT_BIT_DISTANCE) = lossless when --distance is "
                             "EIGHT_BIT_LOSSLESS_UP_TO or less, 'same' = the run's "
                             "--distance, or a number (0 = lossless)")
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
    parser.add_argument("--exclude-folders", default="",
                        help="';'-separated folder NAMES skipped during discovery, in every "
                             "mode. Segment match (case-insensitive), evaluated relative to "
                             "the input folder: '_EXPORT' skips any <input>\\...\\_EXPORT\\... "
                             "tree but never 'My_EXPORT_photos', and never a folder above the "
                             "input — pointing the input AT an excluded name still works. "
                             "Names only: entries containing '\\' or '/' are rejected.")
    parser.add_argument("--export-jxl-folder", type=str, default=None,
                        help="[Modes 6/7] Output folder created under the export marker "
                             "(default: script setting EXPORT_JXL_FOLDER, '16B_JXL'). "
                             "Overrides EXPORT_JXL_FOLDER.")
    parser.add_argument("--encode-tag",     type=str, default=None,
                        choices=["history", "comment", "title", "xmp", "software", "off"],
                        help="The encode record always goes to xmpMM:History; a visible copy: "
                             "history (default: none), comment (Explorer's Comments), title "
                             "(dc:Description, the Title), software (EXIF Software); xmp = "
                             "history. 'off' records nothing AND strips any existing "
                             "gen=/cjxl d=/e= record — the lineage is deliberately discarded")
    parser.add_argument("--d50-patch",      type=str, default=None, choices=["on", "off", "auto"],
                        help="D50 illuminant patch: on (always), off (never), auto (only a near-D50 "
                             "illuminant, e.g. Kodak's ProPhoto.icm)")
    parser.add_argument("--modular",        type=str, default=None, choices=["on", "off"],
                        help="Force the Modular encoder for LOSSY output (default off: cjxl's "
                             "VarDCT). For screenshots/graphics only — measured on real photos: "
                             "equal quality, bigger files, 20-100x slower. Lossless always uses "
                             "Modular regardless.")
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
                        help="Deprecated since v2.12.0 and ignored; see "
                             "--explorer-thumbnail.")
    parser.add_argument("--explorer-thumbnail", choices=["auto", "on", "off"], default=None,
                        help="A small sRGB JPEG of the image stored in the JXL, shown "
                             "by jxl-thumb in Windows Explorer: auto = lossless "
                             "outputs only (default), on = every output, off = none "
                             "(EXPLORER_THUMBNAIL)")
    return parser


_work_dir_problem = _common._work_dir_problem


def _configure_run(parser, args):
    """The configuration step of main(), moved here unchanged (refactor plan 261113, A3):
    the flags into the run's settings, the checks of the command line, the log
    and the tools. Every check keeps its order; the run state reset before it
    stays in main()."""
    global ADOPT_SCAN, CJXL_BUFFERING, CJXL_DISTANCE, CJXL_EFFORT, CJXL_MODULAR
    global D50_PATCH_MODE, DELETE_CONFIRM, DELETE_SKIPPED, DELETE_SOURCE
    global EIGHT_BIT_DISTANCE, EIGHT_BIT_LOSSLESS_UP_TO, ENCODE_TAG_MODE, ICC_PNG_STRATEGY
    global MULTIPAGE_TIFF_MODE, OVERWRITE, OVERWRITE_GUARD, PROVENANCE_CHECK
    global SCAN_DISTANCE, SCAN_SCOPE, TEMP2_DIR, THUMBNAIL_MODE, THUMBNAIL_SUFFIX
    global USE_RAM_FOR_PNG, VERIFY_ROUNDTRIP, WARN_DISCARDED_THUMBNAILS
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
    #
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

    # --restamp-then-sync is a sync that fixes the records first: it must be
    # checked here, and it sets args.sync BEFORE the block below reads it.
    if args.restamp_then_sync and (args.delete_source or args.delete_skipped
                                   or args.overwrite or args.restamp_moved):
        parser.error("--restamp-then-sync cannot be combined with --delete-source, "
                     "--delete-skipped, --overwrite or --restamp-moved: a record "
                     "rewritten in this run must not decide a deletion.")
    if args.restamp_then_sync:
        args.sync = True

    if args.sync:
        OVERWRITE = "smart"
    elif args.overwrite:
        OVERWRITE = True
    # Run-scoped (tests call main() repeatedly in one process): the pairs
    # proved to be the same bytes, see _mark_sync_same_bytes.
    _SYNC_SAME_BYTES.clear()

    if args.delete_source:
        DELETE_SOURCE = True
    if args.delete_confirm_off:
        DELETE_CONFIRM = False
    if args.verify_roundtrip:
        VERIFY_ROUNDTRIP = True
    if args.delete_skipped:
        DELETE_SKIPPED = True
    if args.provenance is not None:
        PROVENANCE_CHECK = args.provenance
    if args.no_overwrite_guard:
        OVERWRITE_GUARD = False
    if args.restamp_moved and (args.delete_source or args.delete_skipped or args.overwrite):
        parser.error("--restamp-moved only rewrites provenance records: it cannot be "
                     "combined with --delete-source, --delete-skipped or --overwrite.")
    if args.no_adopt_scan:
        ADOPT_SCAN = False

    if args.export_subfolder is not None:
        global EXPORT_TIFF_SUBFOLDER
        EXPORT_TIFF_SUBFOLDER = args.export_subfolder

    if args.export_jxl_folder is not None:
        global EXPORT_JXL_FOLDER
        EXPORT_JXL_FOLDER = args.export_jxl_folder.strip()

    if args.distance is not None:
        if not 0 <= args.distance <= 25:
            parser.error("--distance must be between 0 and 25")
        # cjxl clamps: --distance 0.005 and 0.05 were measured producing
        # byte-identical output. Someone asking for 0.02 to get "closer to
        # lossless" gets exactly the 0.05 file and should hear so, rather than
        # believe they bought quality they did not.
        # The clamp WARNING is emitted after setup_logger() below, not here:
        # the module logger has no handler yet, so it would land on raw stderr
        # via logging.lastResort and never reach the log file.
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
    # NOTE: --clean-staging is applied after setup_logger() below (it must be
    # auditable, and it must not run on a dry run).
    # Explicitly empty (--export-marker "") is honored: an empty marker
    # matches nothing (fail closed, see _marker_matches). `if args.export_marker:`
    # kept the script default instead — the modes 6/7 run behaved as if the
    # flag had not been passed (bug #433's encoder twin).
    if args.export_marker is not None:
        global EXPORT_MARKER, EXCLUDE_FOLDERS
        EXPORT_MARKER = args.export_marker

    _excl_raw = [n.strip() for n in args.exclude_folders.split(";") if n.strip()]
    for _n in _excl_raw:
        if "\\" in _n or "/" in _n:
            parser.error(f"--exclude-folders takes folder NAMES, not paths: {_n!r} "
                         "(use a ';'-separated list of bare names, e.g. --exclude-folders \"_EXPORT;temp\")")
    EXCLUDE_FOLDERS = tuple(n.lower() for n in _excl_raw)

    if args.mode in (6, 7):
        _why = _validate_export_folder_name(EXPORT_JXL_FOLDER, EXPORT_MARKER,
                                            EXPORT_TIFF_SUBFOLDER if args.mode == 7 else "")
        if _why:
            parser.error(f"--export-jxl-folder: {_why}")
    if args.encode_tag is not None:
        ENCODE_TAG_MODE = args.encode_tag
    ENCODE_TAG_MODE = _encode_tag_mode()   # "xmp" = "history" (the log says which)
    if args.d50_patch is not None:
        D50_PATCH_MODE = args.d50_patch
    if args.modular is not None:
        CJXL_MODULAR = (args.modular == "on")
    if args.icc_png_strategy is not None:
        ICC_PNG_STRATEGY = args.icc_png_strategy

    if args.multipage_mode is not None:
        MULTIPAGE_TIFF_MODE = args.multipage_mode
    if args.scan_distance is not None:
        if args.scan_distance.strip().lower() == "same":
            SCAN_DISTANCE = None
        else:
            try:
                SCAN_DISTANCE = float(args.scan_distance)
            except ValueError:
                parser.error("--scan-distance takes a number (0 = lossless) or 'same'")
            if not 0 <= SCAN_DISTANCE <= 25:
                parser.error("--scan-distance must be between 0 and 25")
    if args.scan_scope is not None:
        SCAN_SCOPE = args.scan_scope
    if args.eight_bit_distance is not None:
        EIGHT_BIT_DISTANCE = args.eight_bit_distance
    # The flag or the setting edited in the file: checked the same way.
    try:
        EIGHT_BIT_DISTANCE = _parse_eight_bit_distance(EIGHT_BIT_DISTANCE)
        EIGHT_BIT_LOSSLESS_UP_TO = float(EIGHT_BIT_LOSSLESS_UP_TO)
    except (TypeError, ValueError):
        parser.error("--eight-bit-distance / EIGHT_BIT_DISTANCE takes 'auto', 'same' or a "
                     "distance from 0 to 25 (and EIGHT_BIT_LOSSLESS_UP_TO a number)")
    if args.thumbnail_mode is not None:
        THUMBNAIL_MODE = args.thumbnail_mode
    if args.thumbnail_suffix is not None:
        if not args.thumbnail_suffix.strip():
            parser.error("--thumbnail-suffix must not be empty (thumbnail names would collide with page 0)")
        # The suffix is glued straight into the output filename
        # (_page_output_name): a path separator or '..' would write thumbnails
        # OUTSIDE the destination folder. Same parser.error treatment as the
        # other path-affecting inputs above.
        if ("/" in args.thumbnail_suffix or "\\" in args.thumbnail_suffix
                or ".." in args.thumbnail_suffix):
            parser.error("--thumbnail-suffix must be a plain filename suffix "
                         "(no path separators, no '..'): thumbnails would be "
                         "written outside the destination folder")
        THUMBNAIL_SUFFIX = args.thumbnail_suffix
    if args.warn_thumbnail_discard:
        WARN_DISCARDED_THUMBNAILS = True

    # Handle --strip flag - store in global for use in convert_one
    global STRIP_METADATA, EXPLORER_THUMBNAIL
    if args.strip:
        STRIP_METADATA = True
    if args.explorer_thumbnail is not None:
        EXPLORER_THUMBNAIL = args.explorer_thumbnail
    log_file = setup_logger()

    # A second run in the SAME process (the test suite, or anything importing
    # this module) inherited everything below: progress started at [N+1/total],
    # the summary counters doubled, and a source TIFF refused for deletion in
    # the previous run was still refused in this one. Reset every per-run
    # counter where the first run begins (same treatment the decoder gives
    # its equivalents). This lives here and not in _reset_abort: that helper
    # is the one every script shares (jxl_photo_common.py).
    _counter["done"] = 0
    for _k in _d50_patch_count:
        _d50_patch_count[_k] = 0
    _d50_patched_hashes.clear()
    with _multipage_ignored_lock:
        _multipage_ignored["files"] = 0
        _multipage_ignored["pages"] = 0
        _photoshop_left_out["files"] = 0
        _thumbnails_dropped["files"] = 0
        _thumbnails_dropped["pages"] = 0
        _thumbnails_dropped["archived"] = 0
        _discarded_real_page_sources.clear()
        _unarchived_content_sources.clear()
        _discarded_thumb_sources.clear()
        _discard_warned["count"] = 0
        _discard_warned["suppressed"] = 0
    with _SCAN_PLAN_LOCK:
        _SCAN_PLAN.clear()
        _SCAN_REASON.clear()
        _EIGHT_BIT_PLAN.clear()
    for _k in _delete_stats:
        _delete_stats[_k] = 0

    # Rule 2: the guard off PLUS a delete is the exact combination that lost
    # photos — the only copy of another photo overwritten, then its source
    # deleted. Refused before any planning warning or conversion.
    if not OVERWRITE_GUARD and DELETE_SOURCE:
        logger.error("The overwrite guard is off (--no-overwrite-guard or "
                     "OVERWRITE_GUARD = False) and this run deletes sources "
                     "(--delete-source). That combination can overwrite the only "
                     "copy of another photo and then delete the file that could "
                     "rebuild it, so it is refused. Turn the guard back on for "
                     "delete runs.")
        sys.exit(2)

    # Everything below is logged AFTER setup_logger() on purpose: on the
    # module-level logger these have no handler at all, so INFO lines vanish
    # and warnings go to raw stderr instead of the log file.
    _floor = _min_effective_distance(_get_cjxl_cmd() or "cjxl")
    if args.distance is not None and 0 < args.distance < _floor:
        logger.warning(
            f"--distance {args.distance} behaves exactly like "
            f"{_floor}: this cjxl clamps every lossy distance below "
            f"that to the same output. Use --distance 0 for true lossless.")
    if SCAN_DISTANCE is not None and 0 < SCAN_DISTANCE < _floor:
        logger.warning(
            f"SCAN_DISTANCE {SCAN_DISTANCE} behaves exactly like {_floor}: this "
            f"cjxl clamps every lossy distance below that to the same output. "
            f"Use 0 for true lossless.")

    # Sweep the EFFECTIVE staging dir — requiring --staging alongside made the
    # flag silently inert whenever staging came from the script setting (the
    # documented way). Never on a dry run: the leftovers are failed outputs
    # deliberately kept for inspection, and a simulation must not touch disk.
    if args.clean_staging:
        if TEMP2_DIR is None:
            logger.warning("--clean-staging: no staging directory configured; nothing to clean")
        elif args.dry_run:
            logger.info("--clean-staging: skipped (dry run); re-run without --dry-run to sweep")
        else:
            _clean_staging(TEMP2_DIR)

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

    if args.embed_thumbnail:
        logger.warning("--embed-thumbnail is ignored since v2.12.0; "
                       "--explorer-thumbnail (default auto: lossless outputs) "
                       "replaces it.")
    if EXPLORER_THUMBNAIL not in ("auto", "on", "off"):
        logger.error(f"EXPLORER_THUMBNAIL = {EXPLORER_THUMBNAIL!r}: use 'auto', 'on' or 'off'")
        sys.exit(2)
    if (not STRIP_METADATA and _explorer_thumbnail_wanted(CJXL_DISTANCE == 0)
            and (shutil.which("djxl") is None or shutil.which("magick") is None)):
        logger.warning("Explorer thumbnails need djxl and ImageMagick (magick) on PATH; "
                       "this run writes none (EXPLORER_THUMBNAIL / --explorer-thumbnail)")
        EXPLORER_THUMBNAIL = "off"
    if (EXPLORER_THUMBNAIL == "auto" and not STRIP_METADATA and CJXL_DISTANCE > 0
            and _jxl_thumb_installed()):
        logger.info("Explorer thumbnails: jxl-thumb is installed, so lossy outputs get "
                    "one too (EXPLORER_THUMBNAIL = auto; --explorer-thumbnail off: none)")

    if args.restamp_moved and args.provenance is not None:
        logger.warning("--provenance has no effect with --restamp-moved: the restamp "
                       "always requires the content proof (jxlphoto-srcsum).")
    if args.restamp_moved and args.no_overwrite_guard:
        logger.warning("--no-overwrite-guard has no effect with --restamp-moved: it "
                       "only rewrites records, it never overwrites a master.")

    # (No "has no effect" warning: since v2.8.0 every run checks the provenance
    # of each output it is about to overwrite, in every mode —
    # _foreign_overwrite_reason.)
    _warn_src_root = args.input.parent if args.input.is_file() else args.input
    _warn_collapses = _run_collapses_structure(args.mode, args.output, _warn_src_root)
    if args.no_adopt_scan and args.provenance != "adopt":
        logger.warning("--no-adopt-scan has no effect without --provenance adopt: it "
                       "only turns off the verification adoption performs.")

    if args.export_jxl_folder is not None and args.mode not in (6, 7):
        logger.warning(f"--export-jxl-folder only applies to modes 6/7 — ignored in "
                       f"mode {args.mode}.")

    # --strip means "no metadata", and the provenance marker IS metadata, so
    # build_metadata_injection_args returns before writing it. That is the right
    # call — it is what the flag promises — but the consequence has to be said
    # out loud: these outputs carry no record of which source made them, so a
    # later archive-and-delete run in a folder-collapsing mode will refuse them.
    # _run_collapses_structure, not a hand-written `mode == 0 and output`: mode 0
    # with an output folder EQUAL to the source folder writes in place and
    # collapses nothing.
    if STRIP_METADATA and (DELETE_SOURCE or _warn_collapses):
        logger.warning(
            "--strip writes NO provenance marker: these outputs will carry no record "
            "of which source made them. In modes 2/4/5/6/7 — and in mode 0 with an "
            "output folder — a later run with "
            "--delete-source will refuse them (nothing can prove the pairing) until you "
            "pass --provenance adopt; and in EVERY mode --delete-skipped will keep "
            "the sources of such outputs. Drop --strip if you want that protection.")

    if DELETE_SKIPPED and not DELETE_SOURCE:
        logger.warning("--delete-skipped has no effect without --delete-source: it only "
                       "widens which sources the deletion covers. Nothing will be deleted.")
    elif DELETE_SKIPPED and not VERIFY_ROUNDTRIP and not args.dry_run:
        # Not fatal — the structural check still stands between a skip and the
        # unlink — but this is the one combination where the toolkit deletes a
        # master on the strength of a file it did not write.
        # (X5 of the 2026-10-08 audit: the text used to say "a JXL from a
        # different photo with the same name would pass" — untrue since E-1:
        # the gate requires the output's provenance marker to name THIS TIFF.)
        logger.warning(
            "--delete-skipped without --verify-roundtrip: sources whose output already "
            "exists will be deleted once that output carries THIS source's provenance "
            "marker, the TIFF's bytes still match the hash recorded in it, and it "
            "passes the structural check. Nothing decodes the archive itself: add "
            "--verify-roundtrip to compare its pixels before the delete.")

    # --verify-roundtrip needs tools the rest of the encoder does not. Checked
    # up front, and fatally: a verification that silently could not run would
    # be worse than no verification at all — it gates an irreversible delete.
    _needs_decode = VERIFY_ROUNDTRIP or (PROVENANCE_CHECK == "adopt" and ADOPT_SCAN)
    if _needs_decode and not args.dry_run:
        if not DELETE_SOURCE:
            # Only for the flag typed by hand: the setting is on by default,
            # and a run that deletes nothing simply does not need it.
            if args.verify_roundtrip:
                logger.warning("--verify-roundtrip has no effect without --delete-source: "
                               "it is a gate in front of the deletion, not a standalone "
                               "check. Nothing will be verified.")
        else:
            if shutil.which("djxl") is None:
                logger.error("This run needs djxl in PATH: --verify-roundtrip and the "
                             "--provenance adopt scan both decode each JXL back to "
                             "compare it with the source.")
                sys.exit(1)
            try:
                import imagecodecs  # noqa: F401
            except ImportError:
                logger.error("This run needs imagecodecs (pip install imagecodecs): "
                             "PIL quantises 16-bit RGB PNGs to 8-bit, which would fail every "
                             "lossless comparison for the wrong reason.")
                sys.exit(1)
            if CJXL_DISTANCE > 0:
                logger.info(
                    f"Round-trip verification (lossy, distance={CJXL_DISTANCE}): checks that the "
                    f"JXL holds the SAME IMAGE, not that it is a good encode — mean ratio within "
                    f"{VERIFY_LOSSY_MIN_MEAN_RATIO}x and PSNR >= {VERIFY_LOSSY_MIN_PSNR} dB.")
            else:
                logger.info("Round-trip verification (lossless): decoded pixels must be identical.")
    return log_file, _needs_decode


def _output_root(args):
    """The output root of a run: the output positional in modes 0/1/2, else
    the input folder (a single TIFF's own folder in modes 0/1); the input in
    every other mode. One answer for _find_sources and _planned_output."""
    if args.mode in (0, 1) and args.input.is_file():
        return args.output or args.input.parent
    if args.mode in (0, 1, 2):
        # Mode 0: output_root = output_dir if given, else same folder as each TIFF
        return args.output or args.input
    return args.input


def _find_sources(args):
    """Which TIFFs this run takes and the output root, per mode: moved out of
    main() unchanged (refactor plan 261113, A3). One place that answers
    "which files", so the wrapper can ask it instead of guessing (C1)."""
    if args.mode in (0, 1) and args.input.is_file():
        tiffs = [args.input]
    elif args.mode in (0, 1):
        # Directory input: flat (non-recursive)
        tiffs = find_files_mode0(args.input)
    elif args.mode == 2:
        # Mode 2: recursive, all files to output_root (single file also accepted)
        if args.input.is_file():
            tiffs = [args.input]
        else:
            tiffs = find_tiffs_recursive(args.input)
        # mkdir deferred until after the dry-run check — a simulation must
        # not create folders on disk.
    elif args.mode == 6:
        tiffs = find_tiffs_mode6(args.input)
    elif args.mode == 7:
        tiffs = find_tiffs_mode7(args.input)
    elif args.mode == 8:
        # Mode 8: in-place recursive + delete source (single file also accepted)
        if args.input.is_file():
            tiffs = [args.input]
        else:
            tiffs = find_tiffs_recursive(args.input)
    else:
        # Modes 3/4/5: a single FILE input is valid too (find_tiffs_recursive
        # only works on directories and would silently find nothing).
        if args.input.is_file():
            tiffs = [args.input]
        else:
            tiffs = find_tiffs_recursive(args.input)
    return tiffs, _output_root(args)


def _planned_output(t: Path, args):
    """Where a run with these arguments writes t's JXL, or None when the mode
    leaves t out (outside the export marker in modes 6/7). It is the MAIN
    JXL: the pages of a split TIFF land next to it, under names only the open
    TIFF can tell (convert_multipage). The first half of main()'s _plan_one,
    moved here so main() and the wrapper's collision scan get one answer
    (refactor C1). Needs args.input, args.output and args.mode; the marker
    and folder settings are this module's globals."""
    output_root = _output_root(args)
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
    return main_jxl


def _planned_sources(args, read_markers=True) -> list:
    """The TIFFs a run with these arguments takes (_find_sources): what the
    wrapper counts and scans before a run, asking this script instead of
    copying its rules (refactor C1). Needs args.input, args.output and
    args.mode; the marker and folder settings are this module's globals.
    read_markers: the recompressor's switch; this list reads no marker."""
    return list(_find_sources(args)[0])


def _planned_pairs(args, read_markers=True) -> list:
    """(TIFF, its main JXL) for each TIFF _planned_sources gives, the ones
    the mode leaves out dropped: what the wrapper's collision scan asks
    (refactor C1). The pages of a split TIFF are the scan's own probe."""
    pairs = []
    for t in _planned_sources(args, read_markers):
        out = _planned_output(t, args)
        if out is not None:
            pairs.append((t, out))
    return pairs


def _guard_existing_outputs(args, all_items, _restamped, failed_files):
    """The guard over outputs that already exist (provenance, overwrite, the
    strict guard of a run that collapses folders), moved out of main()
    unchanged (refactor plan 261113, A3). It may drop items from all_items."""
    provenance_blocked = []
    provenance_failures = []
    # Outputs adopt has decided to stamp. Filled here, WRITTEN much later — see
    # the stamping block after the delete confirmation.
    provenance_to_stamp = []
    _src_root = args.input.parent if args.input.is_file() else args.input
    _strict_guard = DELETE_SOURCE and _run_collapses_structure(args.mode, args.output, _src_root)
    if _strict_guard:
        _existing = sorted({item[1] for item in all_items if item[1].exists()})
    else:
        # Only what will really be overwritten: an up-to-date output is a
        # SKIP and writes nothing, so it has nothing to refuse.
        _existing = sorted({item[1] for item in all_items
                            if item[1].exists() and not _would_skip(item[0], item[1])})
    if _existing:
        _existing_set = set(_existing)
        logger.info(f"Provenance: checking {len(_existing)} existing output(s) "
                    f"(--provenance {PROVENANCE_CHECK})...")
        _marks = _read_source_markers_batch(_existing)
        _bad_sources = {}
        _adopted, _adopted_blind, _would_adopt = [], [], []
        for t, j, page_idx, *_rest in all_items:
            if j not in _existing_set:
                continue
            info = _marks.get(str(j)) or {"src": None, "srcsum": None}
            if j in _restamped:
                # Rewritten (or, in a dry run, about to be) by --restamp-then-sync:
                # the preview must judge the record the real run will see.
                info = dict(info, src=_restamped[j])
                info.pop("unread", None)    # the restamp itself read and wrote it
            if not _strict_guard:
                _why_x = _foreign_overwrite_reason(info, t, j)
                if _why_x:
                    _bad_sources.setdefault(str(t), (j, _why_x))
                continue
            if _provenance_ok(info, t, PROVENANCE_CHECK):
                continue
            if info.get("unread"):
                # X-A: "cannot tell" is not "no marker" — adopt must not stamp
                # (or the strict guard wave through) an output whose markers
                # were never read; OVERWRITE_GUARD never relaxes this guard.
                _bad_sources.setdefault(
                    str(t), (j, "its provenance markers could not be read (the "
                                "exiftool batch failed or stalled) — re-run once "
                                "exiftool reads the folder again"))
                continue
            _unmarked = not (info.get("src") or info.get("srcsum"))
            # An archive built before these markers existed carries none.
            # That is a gap in what we KNOW, not evidence of a conflict, so
            # `adopt` resolves it by PROVING the pairing rather than
            # assuming it — and only for that case. A marker that MISMATCHES
            # is still refused: adopt relaxes "I cannot tell", never "I can
            # tell it is wrong".
            if PROVENANCE_CHECK == "adopt" and _unmarked:
                # A dry run neither scans nor stamps. The scan decodes every
                # unmarked output (hours on a real library) and the stamp
                # WRITES to the file — both are things a simulation must not
                # do, and reporting the count is what the user came for.
                if args.dry_run:
                    _would_adopt.append((t, j))
                    continue
                if not ADOPT_SCAN:
                    _adopted_blind.append((t, j))
                    continue
                _ok_v, _detail = _verify_roundtrip_page(t, page_idx, j, _distance_for(t))
                if _ok_v:
                    _adopted.append((t, j))
                    continue
                # The scan verifies against the CURRENT --distance, so the
                # likeliest mass refusal is not a foreign archive at all:
                # an archive written at d=0.1 can never survive THIS run's
                # d=0 pixel comparison. Name that cause and its remedies —
                # mirror of the delete-gate hint in _delete_sources.
                _bad_sources.setdefault(
                    str(t), (j, f"the adopt scan says this output is not this "
                                f"source ({_detail}) — if the archive was written "
                                f"at a different --distance than this run "
                                f"(d={_distance_for(t)}), re-run with the archive's "
                                f"distance, or pass --no-adopt-scan to adopt "
                                f"without the scan" + _scan_distance_hint(t)))
                continue
            _why = ("no provenance marker — this archive predates them; re-run with "
                    "--provenance adopt to verify and stamp it"
                    if _unmarked else "it was made from a different source")
            _bad_sources.setdefault(str(t), (j, _why))
        for _t, _j in _adopted_blind:
            logger.warning(f"  ADOPTED without proof (--no-adopt-scan) | {_j.name}")
        if _would_adopt:
            logger.info(
                f"Dry run: {len(_would_adopt)} existing output(s) carry no provenance "
                f"record. A real run would decode and compare each one against its "
                f"source before adopting and stamping it"
                + ("" if ADOPT_SCAN else " — except that --no-adopt-scan is set, so "
                                          "they would be adopted WITHOUT that proof")
                + ". Any that failed the check would be REFUSED, so this count is an "
                  "upper bound, and nothing has been written or verified yet.")
        if _adopted or _adopted_blind:
            # WHAT to stamp is decided here; the stamping itself happens
            # after the delete confirmation (see provenance_to_stamp). It
            # rewrites the user's existing archive, so it must not run on a
            # dry run, and it must not run when the user then answers "no"
            # to the deletion this whole check exists to gate.
            provenance_to_stamp = list(_adopted) + list(_adopted_blind)
            logger.info(
                f"Provenance: adopting {len(provenance_to_stamp)} existing output(s)"
                + (f", {len(_adopted)} verified by the adopt scan" if _adopted else "")
                + " — they will be stamped once the run is confirmed.")

        if _bad_sources:
            provenance_blocked = sorted(_bad_sources)
            if _strict_guard:
                logger.error(
                    f"REFUSING {len(provenance_blocked)} file(s): their output already "
                    f"exists and did not come from them. Converting would overwrite "
                    f"someone else's archive, and --delete-source would then destroy "
                    f"the photo that archive held.")
            else:
                logger.error(
                    f"REFUSING {len(provenance_blocked)} file(s): their output already "
                    f"exists and belongs to a DIFFERENT file. Converting would "
                    f"overwrite that archive — and if an earlier run deleted its "
                    f"source, the photo it holds exists nowhere else.")
            for _sp in provenance_blocked[:10]:
                _out, _why = _bad_sources[_sp]
                logger.error(f"    {_sp}")
                logger.error(f"      -> {_out} ({_why})")
            if len(provenance_blocked) > 10:
                logger.error(f"    ... and {len(provenance_blocked) - 10} more")
            if _strict_guard:
                logger.error(
                    "  These files were NOT converted and NOTHING was deleted. Rename "
                    "them, pick a mode that keeps folder structure (0/1/3/8), or drop "
                    "--delete-source." +
                    ("" if PROVENANCE_CHECK == "content" else
                     " If you MOVED the sources, re-run with --provenance content."))
                logger.error(f"  Details: {GUARD_DOC_URL}")
            else:
                logger.error(
                    "  These files were NOT converted and NOTHING was deleted. Rename "
                    "the source (or the existing output, if it is really a different "
                    "photo you want to keep apart); delete the existing output only "
                    "if you are sure it is obsolete." +
                    ("" if PROVENANCE_CHECK == "content" else
                     " If you MOVED the sources, re-run with --provenance content."))
                for _l in _guard_refusal_hint(True):
                    logger.error(_l)
            _blocked = set(provenance_blocked)
            all_items = [it for it in all_items if str(it[0]) not in _blocked]
            # A refusal is a FAILURE, not a quiet skip: the file was not
            # converted and needs a human. Counted into err (exit 1) and
            # listed in the summary's failures so a scheduled run and the
            # wrapper's manifest recap both surface it.
            for _sp in provenance_blocked:
                _o, _w = _bad_sources[_sp]
                provenance_failures.append(
                    (_sp, f"refused: output {_o} already exists and {_w}"))
            failed_files.extend(provenance_failures)
    return all_items, provenance_blocked, provenance_failures, provenance_to_stamp


def main():
    _utf8_console()      # before argparse prints anything (#661)
    parser = build_parser()
    args = parser.parse_args()

    global ICC_CACHE_DIR_OVERRIDE
    global _gen_divergence_logged
    _gen_divergence_logged = False

    # Every CLI-mutated, run-scoped global is reset UNCONDITIONALLY here,
    # BEFORE the flag overrides below (the recompressor's canonical R-2
    # pattern, same round): the assignments further down only fire when their
    # flag was passed, so a second main() in the SAME process (the test
    # suite, or anything importing this module) inherited the first run's
    # armed state — a `--delete-source --delete-confirm-off` run left
    # DELETE_SOURCE on with no confirmation for the next run, and `--staging`
    # leaked TEMP2_DIR. _RUN_DEFAULTS restores each global to its SCRIPT
    # setting (captured at import), so a user-edited setting survives while
    # cross-run leakage does not.
    for _gname, _gvalue in _RUN_DEFAULTS.items():
        globals()[_gname] = _gvalue
    _PAGE_PIXELS.clear()
    _PAGE_CHANNELS.clear()

    # ICC cache override and clearing must be processed before any logging or conversion.
    if args.icc_cache_dir is not None:
        ICC_CACHE_DIR_OVERRIDE = Path(args.icc_cache_dir)
    if args.clear_icc_cache:
        cleared = _clear_icc_cache()
        # UTF-8 like setup_logger's console (which this early exit never
        # reaches): through a pipe (the wrapper's menu C) stdout is the ANSI
        # code page, and a cache folder under a Japanese user name raised
        # UnicodeEncodeError after the cache was cleared (#605).
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print(f"ICC cache {'cleared' if cleared else 'was already empty'}: {_icc_cache_dir()}")
        return

    log_file, _needs_decode = _configure_run(parser, args)

    _modular_label = "modular" if (CJXL_MODULAR and CJXL_DISTANCE > 0) else "VarDCT"
    _delete_label  = f"delete_source=ON (confirm={'ON' if DELETE_CONFIRM else 'OFF'})" if DELETE_SOURCE else "delete_source=OFF"
    _overwrite_str = "sync" if args.sync else ("yes" if args.overwrite else ("smart" if OVERWRITE == "smart" else "no"))
    _tag_label     = ENCODE_TAG_MODE  # xmp, software, or off
    # split_all encodes every page there is, thumbnails included, and ignores
    # THUMBNAIL_MODE entirely (see convert_multipage). Printing the raw setting
    # made this line say "Thumbnail: exclude" for a run whose very next lines
    # write *_thumbnail.jxl — and this banner is documented as showing the
    # settings that are ACTIVE.
    _thumb_label = ("include (forced by split_all)"
                    if MULTIPAGE_TIFF_MODE == "split_all" else THUMBNAIL_MODE)
    logger.info(
        f"Mode: {args.mode} | Effort: {CJXL_EFFORT} | "
        f"Distance: {CJXL_DISTANCE} ({'lossless' if CJXL_DISTANCE == 0 else f'lossy/{_modular_label}'}) | "
        f"RAM PNG: {USE_RAM_FOR_PNG} | Staging: {TEMP2_DIR or 'disabled'} | "
        f"Overwrite: {_overwrite_str} | Tag: {_tag_label} | D50 patch: {D50_PATCH_MODE} | {_delete_label} | "
        f"Multi-page: {MULTIPAGE_TIFF_MODE} | Thumbnail: {_thumb_label} | Workers: {args.workers}"
    )
    logger.info(f"Input: {args.input}")
    if SCAN_DISTANCE is not None and SCAN_DISTANCE != CJXL_DISTANCE:
        logger.info(f"Film scans ({'raw ones' if str(SCAN_SCOPE).lower() == 'raw' else 'all'}): "
                    f"distance {SCAN_DISTANCE}"
                    f"{' (lossless)' if SCAN_DISTANCE == 0 else ''} — SCAN_DISTANCE")
    if not OVERWRITE_GUARD:
        logger.warning("Overwrite guard OFF: an existing output made from another "
                       "source path is overwritten (and logged) instead of refused.")

    # Collect input files
    # Modes 0 and 1 accept a single file OR a directory
    tiffs, output_root = _find_sources(args)

    # Mode 1 always writes to <source folder>/converted_jxl/, so an output
    # positional is silently discarded. Accepting it without a word looked like
    # the destination had been honored.
    if args.mode == 1 and args.output is not None:
        logger.warning(f"--mode 1 ignores the output folder ({args.output}): "
                       f"outputs go to <source folder>/{CONVERTED_JXL_FOLDER}/. "
                       f"Use --mode 2 to write everything into one folder.")

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
        main_jxl = _planned_output(t, args)

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

    _planned_outputs.clear()        # run-scoped (#615)
    _swap_refusals.clear()
    _abort_on_duplicate_outputs([(item[0], item[1]) for item in all_items])
    if not args.dry_run:
        _sweep_orphan_temps(item[1] for item in all_items)
        _record_planned_outputs((item[0], item[1]) for item in all_items)

    # --restamp-moved stops here: no preflight, no conversion, no delete.
    if args.restamp_moved:
        sys.exit(_restamp_moved(all_items, args.dry_run)[0])
    # --restamp-then-sync: the same rewrite, BEFORE the overwrite guard below
    # reads the records, then the run goes on as --sync.
    _restamped, _restamp_failures = {}, []
    if args.restamp_then_sync:
        _rc_restamp, _restamped, _restamp_failures = _restamp_moved(all_items, args.dry_run)
        failed_files.extend(_restamp_failures)

    # Smart sync: a newer TIFF with the bytes its JXL was made from is up to
    # date. Filled BEFORE the guard below and the preflight read _would_skip
    # (the set itself is cleared at the start of the run, where it is also
    # filled by the thumbnail guard).
    _mark_sync_same_bytes(all_items, args.dry_run)

    # Cross-RUN output collision. The guard above only sees collisions inside
    # THIS run; an output written by an earlier run can belong to a different
    # source entirely, and overwriting it destroys the earlier photo for good
    # when an earlier run already deleted that photo's own source.
    #
    # Two levels. Deletion armed in a collapsing mode (the strict guard): EVERY
    # existing output must prove it came from this source — a markerless one
    # too (adopt heals those). Everywhere else (no deletion, or a mode that
    # keeps folders): every output this run is about to OVERWRITE is checked,
    # and one whose marker names ANOTHER source is refused. That second level
    # is the half of #268 that was missing: the overwrite is the destructive
    # step, not this run's deletion — a plain sync (every scheduled preset is
    # one) destroyed the archive of a photo deleted by an earlier run, and
    # foto.tif / foto.tiff collide in mode 8 as well.
    all_items, provenance_blocked, provenance_failures, provenance_to_stamp = _guard_existing_outputs(args, all_items, _restamped, failed_files)

    _counter["total"] = len(all_items)

    # Film scans at their own distance, by reason — before the first encode,
    # so a wrong guess can be stopped (dry runs included).
    _to_write = {os.path.normcase(str(t)): t for t, j, *_ in all_items
                 if not _would_skip(t, j)}.values()
    _log_scans_at_scan_distance(_scans_at_scan_distance(_to_write), done=False)
    _log_eight_bit_at_other_distance(_eight_bit_at_other_distance(_to_write), done=False)

    # Dry run
    if args.dry_run:
        # Predict the skips convert_one makes at conversion time (same
        # _would_skip decision, TOCTOU fallback included): ok=len(all_items)
        # counted outputs that would only be SKIPped and skipped= undercounted
        # by the same amount — the wrapper sums these toplines, so the lie
        # scaled across every manifest entry.
        _sync_skips = sum(1 for t, j, *_ in all_items if _would_skip(t, j))
        for t, j, page_idx, is_thumb, subfiletype, samples in all_items:
            thumb_label = " [thumbnail]" if is_thumb else ""
            gray_label = " [grayscale]" if samples == 1 else ""
            logger.info(f" DRY | {t.name} page{page_idx}{thumb_label}{gray_label} > {j}")
        logger.info(f"Dry run: {len(all_items)} output(s) would be generated from {len(tiffs)} TIFF(s).")
        if _sync_skips:
            logger.info(f"Dry run: {_sync_skips} of them up to date — smart sync would SKIP them (no reconvert).")
        # A dry run of a DELETE run must say so. Without this the flag that
        # destroys originals was the one thing the simulation never mentioned.
        if DELETE_SOURCE:
            _srcs = {str(t) for t, *_ in all_items}
            logger.warning(
                f"Dry run: --delete-source is ARMED. Up to {len(_srcs)} source TIFF(s) "
                f"would be DELETED, each only after its output is written to the "
                f"mode's destination and passes the integrity check"
                + (" and the round-trip comparison" if VERIFY_ROUNDTRIP else "") + ".")

        # Preview --delete-skipped. A dry run returns before process_group, so
        # without this the one destructive option that acts on files this run
        # does NOT touch would have no preview at all. Uses the cheap checks
        # only (the round-trip is not run here and the report says so) — but
        # the provenance proof is the SAME predicate the real delete gate
        # runs (`_skipped_archive_proof`), so the preview can never promise a
        # deletion the gate would refuse (E-1: a foreign same-named archive
        # used to be previewed as an already-archived source).
        if DELETE_SOURCE and DELETE_SKIPPED:
            _by_src: Dict[str, list] = {}
            for t, j, page_idx, _th, _sf, _sp in all_items:
                _by_src.setdefault(str(t), []).append(j)
            # Only the FULLY-skipped sources can be deleted by this flag, so
            # only their finals go into the batched marker read.
            _fully_skipped = {_src: _finals for _src, _finals in _by_src.items()
                              if all(_would_skip(Path(_src), Path(j)) for j in _finals)}
            _proof = _skipped_archive_proof(
                [(Path(_src), Path(j)) for _src, _finals in _fully_skipped.items()
                 for j in _finals])
            _would_delete, _would_keep = [], []
            for _src, _finals in _fully_skipped.items():
                if os.path.normcase(_src) in _discarded_real_page_sources:
                    _would_keep.append((_src, "pages were discarded"))
                elif os.path.normcase(_src) in _unarchived_content_sources:
                    _would_keep.append(
                        (_src, _unarchived_content_sources[os.path.normcase(_src)]))
                elif not all(_proof.get((os.path.normcase(_src),
                                         os.path.normcase(str(j))), False)
                             for j in _finals):
                    _would_keep.append((_src, "output carries no matching "
                                                "provenance marker, or the TIFF "
                                                "changed since it was written — "
                                                "it cannot prove it is an "
                                                "archive of THIS photo"))
                elif all(_verify_jxl_integrity(j) for j in _finals):
                    _would_delete.append(_src)
                else:
                    _would_keep.append((_src, "output failed the integrity check"))
            if _would_delete or _would_keep:
                logger.warning(
                    f"Dry run: --delete-skipped would DELETE {len(_would_delete)} "
                    f"already-archived source(s), and keep {len(_would_keep)}.")
                for _s in _would_delete[:10]:
                    logger.warning(f"  would DELETE | {_s}")
                if len(_would_delete) > 10:
                    logger.warning(f"  ... and {len(_would_delete) - 10} more")
                for _s, _why in _would_keep[:10]:
                    logger.info(f"  would KEEP ({_why}) | {_s}")
                if VERIFY_ROUNDTRIP:
                    logger.warning(
                        "  NOTE: the round-trip check was NOT run in this preview — it "
                        "decodes every file, so the real run may keep more than this.")
        # A dry run is exactly where the user checks whether pages get dropped,
        # so the discard totals must print here too — the real-run summary below
        # is never reached.
        _log_discard_summary()
        emit_summary_json(
            args.summary_json,
            # Up-to-date outputs are SKIPs, not conversions: ok must exclude
            # them and skipped must include them, exactly like the real run
            # (which counts them at 5412 and reports ok=synced).
            ok=len(all_items) - _sync_skips, overwritten=0,
            skipped=skipped_files + multipage_skipped + _sync_skips,
            # + the refusals. They are already in `failures`, and reporting
            # errors:0 next to a non-empty failure list made the wrapper's
            # recap contradict itself. The refusal is a real prediction: those
            # files would not be converted. The EXIT CODE is untouched — a
            # simulation still returns 0, so a scripted dry-run-then-run flow
            # keeps working.
            errors=analyze_errors + len(provenance_failures) + len(_restamp_failures),
            log_file=log_file,
            extras={
                "Thumbnails excluded": _thumbnails_dropped["pages"],
                "Multipage pages ignored": _multipage_ignored["pages"],
                "Records restamped (moved folder)": len(_restamped),
            },
            failures=failed_files,
            unreadable=unreadable_files,
            dry_run=True,
        )
        return

    # Create the mode-2 output dir only for real runs (dry-run must not write)
    if args.mode == 2 and not args.input.is_file():
        output_root.mkdir(parents=True, exist_ok=True)

    # Deletion is available in EVERY mode, not just 8: the mode decides where
    # the JXL lands, deleting the source is a separate opt-in. The confirmation
    # therefore has to be charged here for every mode too, or a direct
    # `--mode 3 --delete-source` would delete without ever asking.
    if DELETE_SOURCE:
        _where = ("next to each source TIFF" if args.mode == 8
                  else "in the mode's destination folder")
        logger.info(f"DELETE_SOURCE=True (mode {args.mode}): source TIFFs will be deleted "
                    f"after their JXL is written {_where} and verified")
        if VERIFY_ROUNDTRIP:
            logger.info("  Round-trip verification is ON: each JXL is decoded and compared "
                        "against its source before the source is deleted")
        # The token is charged only when the plan can actually delete
        # something (#432's decoder twin): a no-TTY re-run of an
        # already-archived folder used to be asked for a token nobody could
        # type and exit 3 forever. Fail closed — _plan_would_delete_source
        # says yes as soon as anything might be deleted, including the
        # --delete-skipped cases (whose real gates, E-1 included, still run in
        # the delete gate itself). A dry run never reaches this block at all.
        if DELETE_CONFIRM and _plan_would_delete_source(all_items):
            # Lossy when ANY file of the run is: film scans can be lossless
            # (SCAN_DISTANCE) in a lossy run, or the other way round.
            with _SCAN_PLAN_LOCK:
                _planned_d = [d for _k, d in _SCAN_PLAN.values()]
            is_lossy = CJXL_DISTANCE > 0 or any(d > 0 for d in _planned_d)
            if not confirm_deletion_tiff(is_lossy):
                logger.info("Deletion not confirmed -- exiting.")
                sys.exit(3)

        # Stamp the adopted outputs, so the archive heals in one pass: without
        # this a SKIPPED file is deleted with nothing rewritten, and the next
        # run would have to adopt it all over again.
        #
        # Deliberately HERE and not where the decision was made. This writes to
        # files the user already owns, so it must come after BOTH gates that can
        # still call the run off — --dry-run (which never reaches this line) and
        # the confirmation above. Stamping first meant a declined confirmation
        # exited 3 having already modified the archive.
        if provenance_to_stamp:
            _stamped = 0
            for _t, _j in provenance_to_stamp:
                _why = _rewrite_master_relation(
                    _j, _provenance_marker_args(_t), _source_path_id(_t))
                if _why is None:
                    _stamped += 1
                else:
                    logger.debug(f"Could not stamp {_j}: {_why}")
            logger.info(
                f"Provenance: stamped {_stamped} of {len(provenance_to_stamp)} adopted "
                f"output(s) — later runs check them strictly.")
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
            _preflight_space(_preflight_groups(groups), CJXL_DISTANCE, CJXL_EFFORT,
                             TEMP2_DIR)
        except Exception as e:
            # An advisory check must never be the thing that kills a run.
            logger.debug(f"Preflight skipped: {e}")

    ok = skipped = overwritten = synced = aborted = 0
    err = analyze_errors + len(provenance_failures) + len(_restamp_failures)  # count TIFFs that couldn't be analyzed at planning time
    _reset_abort()  # a fresh run must not inherit a previous one's latch
    _source_identity.clear()

    # ONE pool for the whole run. Feeding it folder by folder meant a folder with
    # fewer files than --workers could never fill the pool, and every folder
    # boundary drained it — modes 3/5/6/7 create one output folder per shoot, so
    # a big library ran at a fraction of the requested concurrency. The staging
    # move is still per-folder and still in bulk; it now fires when that folder's
    # last file lands (see process_group).
    results = process_group(all_items, args.workers, args.mode)

    # What this run wrote, for the film-scan and thumbnail lines of the summary.
    _written = [r for r in results if r[1] in ("ok", "overwrite")]
    _scans_written = _scans_at_scan_distance(
        {os.path.normcase(str(r[0][0])): r[0][0] for r in _written}.values())
    _eight_written = _eight_bit_at_other_distance(
        {os.path.normcase(str(r[0][0])): r[0][0] for r in _written}.values())
    _thumbs_written = {}
    for r in _written:
        if Path(str(r[2])).stem.endswith(THUMBNAIL_SUFFIX):
            _thumbs_written[os.path.normcase(str(r[0][0]))] = (
                _thumbs_written.get(os.path.normcase(str(r[0][0])), 0) + 1)
    # Pages an earlier encode of a rewritten TIFF left beside its new archive.
    _stale_pages = _clean_stale_split_pages(all_items, results)

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
        else:
            # A status no branch above counts (a new one, forgotten here) is an
            # error, never silence (refactor plan 261113, A4).
            err += 1
            failed_files.append((str(result[0][0]), f"internal: status {status!r} is not counted"))

    # Account for files intentionally skipped by the multipage policy AND for
    # mode 6/7 files that fell outside the export marker. The DRY-RUN summary
    # already counts both (skipped_files + multipage_skipped), and the wrapper
    # aggregates the ##JXLSUM## lines across dry and real runs — so the real
    # run must report the same total or the two summaries contradict each
    # other.
    skipped += skipped_files + multipage_skipped

    logger.info(f"\n{'-'*50}")
    if args.sync:
        logger.info(f"SYNC done: {synced} reconverted | {skipped} up to date | {err} errors")
        logger.info("  -> Reconverted: TIFFs newer than their existing JXL")
        logger.info("  -> Up to date: JXL is newer than or equal to TIFF")
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
    if _photoshop_left_out["files"]:
        logger.info(
            f"Photoshop resources: {_photoshop_left_out['files']} page(s) have a block over "
            f"{PRIVATE_TAG_MAX_BYTES:,} bytes (a preview, print settings) that is not kept; "
            f"pixels, ICC, EXIF, XMP and IPTC are. A file with saved paths is named above.")
    _log_scans_at_scan_distance(_scans_written, done=True)
    _log_eight_bit_at_other_distance(_eight_written, done=True)
    if _thumbs_written and MULTIPAGE_TIFF_MODE.lower() == "split":
        # Kept by the mode (auto keeps every thumbnail page but Capture One's),
        # so no surprise extra *_thumbnail.jxl files.
        logger.info(
            f"Thumbnails: kept {sum(_thumbs_written.values())} thumbnail page(s) of "
            f"{len(_thumbs_written)} TIFF(s) as *{THUMBNAIL_SUFFIX}.jxl (--thumbnail-mode "
            f"{THUMBNAIL_MODE}; exclude leaves them out).")

    # D50 patch summary
    applied = _d50_patch_count["applied"]
    d50_skipped = _d50_patch_count["skipped"]
    already_correct = _d50_patch_count["already_correct"]
    skipped_needed = _d50_patch_count["skipped_needed"]
    applied_already_correct = _d50_patch_count["applied_already_correct"]
    skipped_already_correct = _d50_patch_count["skipped_already_correct"]
    other_illuminant = _d50_patch_count["other_illuminant"]

    # Total files analyzed for D50 = those where patch was applied + those skipped
    total_analyzed = applied + d50_skipped

    if total_analyzed > 0:
        if D50_PATCH_MODE == "off":
            # For mode off, we still tracked correctness so user knows how many would have needed patch
            logger.info(f"D50 patch: {already_correct} already correct | {skipped_needed} would have needed (mode: off)"
                        + (f" | {other_illuminant} other illuminant (left as is)" if other_illuminant > 0 else ""))
        else:
            # Files that were actually patched (had wrong D50)
            actually_patched = applied - applied_already_correct
            # Total files that needed patching vs total that were already correct
            total_needed_patch = actually_patched + skipped_needed
            # Counters increment per page in multipage splits; unique profiles
            # deduplicates pages that share the same ICC.
            unique_profiles = len(_d50_patched_hashes)
            unique_label = f" ({unique_profiles} unique profiles)" if unique_profiles != actually_patched else ""
            logger.info(f"D50 patch: {actually_patched} applied{unique_label} | {skipped_already_correct} skipped (already correct) | {total_needed_patch} needed patch, {already_correct} already correct (mode: {D50_PATCH_MODE})"
                        + (f" | {other_illuminant} other illuminant (left as is)" if other_illuminant > 0 else ""))

    if DELETE_SOURCE:
        logger.info(f"Sources DELETED: {_delete_stats['deleted']}"
                    + (f" ({_delete_stats['deleted_archived']} already archived)"
                       if _delete_stats["deleted_archived"] else "")
                    + f" | kept by a gate: {_delete_stats['kept']}"
                    + (f" | refused (output belongs to another source): "
                       f"{len(provenance_blocked)}" if provenance_blocked else ""))

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
            "Thumbnails kept": sum(_thumbs_written.values()),
            "Old thumbnail pages removed": _stale_pages["removed"],
            "Old pages kept (check them)": _stale_pages["kept"],
            "Film scans at their own distance": sum(len(v) for v in _scans_written.values()),
            "Film scans guessed (no Software, no camera EXIF)":
                len(_scans_written.get("guessed") or ()),
            "Multipage pages ignored": _multipage_ignored["pages"],
            "D50 patched": (applied - applied_already_correct) if D50_PATCH_MODE != "off" else 0,
            "Not attempted (run aborted)": aborted,
            _common.SUMMARY_DELETED: _delete_stats["deleted"],
            _common.SUMMARY_DELETED_ARCHIVED: _delete_stats["deleted_archived"],
            _common.SUMMARY_KEPT: _delete_stats["kept"],
            _common.SUMMARY_REFUSED: len(provenance_blocked),
            "Records restamped (moved folder)": len(_restamped),
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
    if err > 0 or _swap_refusals:
        sys.exit(1)

if __name__ == "__main__":
    main()
