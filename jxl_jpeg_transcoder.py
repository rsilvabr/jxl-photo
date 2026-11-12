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
import base64
import logging
import tempfile
import threading
import hashlib
import argparse
import functools
import json
import re
import struct
import uuid
import zlib
import contextlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time
from typing import Optional

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

# MIME types named in the xmpMM:History entry (_history_args).
_HISTORY_MIME = _common._HISTORY_MIME


def _jpeg_main_eoi(file_path: Path) -> bool:
    """Does this JPEG reach the EOI of its MAIN image?

    Walks the marker chain from the SOI: segments with a length (APPn — where
    an EXIF thumbnail with its own SOI/EOI lives —, DQT, DHT, SOFn, COM, ...)
    are skipped by that length; after each SOS the entropy-coded data is
    scanned for the next real marker (FF00 stuffing, RSTn and fill bytes are
    part of the data). True only on reaching EOI; a file that ends first, or
    whose structure breaks, is truncated or broken. Bytes after the EOI
    (Motion Photo payloads, appended data) are fine: they are never read.
    """
    import mmap
    try:
        if file_path.stat().st_size < 4:
            return False
        with open(file_path, "rb") as f, \
                mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            n = len(m)
            if m[0:2] != b"\xff\xd8":
                return False
            i = 2
            while True:
                if i >= n:
                    return False
                if m[i] != 0xFF:
                    # Extraneous bytes between segments: decoders skip them
                    # to the next 0xFF ("Corrupt JPEG data: N extraneous
                    # bytes before marker"), and a bit-exact reconstruction
                    # carries them over — resync instead of refusing.
                    i = m.find(b"\xff", i)
                    if i < 0:
                        return False
                while i < n and m[i] == 0xFF:      # fill bytes before a marker
                    i += 1
                if i >= n:
                    return False
                marker = m[i]
                i += 1
                if marker == 0x00:                 # not a marker: keep resyncing
                    continue
                if marker == 0xD9:                 # EOI of the main image
                    return True
                if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                    continue                       # TEM / RSTn: no length
                if i + 2 > n:
                    return False
                seglen = (m[i] << 8) | m[i + 1]
                if seglen < 2:
                    return False
                i += seglen
                if i > n:
                    return False
                if marker != 0xDA:                 # not SOS: next segment
                    continue
                while True:                        # entropy-coded data
                    j = m.find(b"\xff", i)
                    if j < 0 or j + 1 >= n:
                        return False
                    nxt = m[j + 1]
                    if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
                        i = j + 2                  # stuffing / restart marker
                        continue
                    if nxt == 0xFF:
                        i = j + 1                  # fill byte: look again
                        continue
                    i = j                          # a real marker ends the scan
                    break
    except (OSError, ValueError):
        return False


def _verify_file_integrity(file_path: Path) -> bool:
    """Verify output file integrity before deleting source.

    Branch selection is by content signature where one is unambiguous
    (beside-final temps end in ".tmp" since the item-26 fix — a truncated
    temp is exactly what this check exists to catch), falling back to the
    extension for the rest (a bare ".tmp" of unknown kind refuses — no
    false pass for garbage):
    - JXL: Valid JXL signature + well-formed box chain ending at EOF
    - JPEG: Valid SOI + the EOI that closes the MAIN image, found by walking
      the segment chain (an EXIF thumbnail's EOI does not count;
      trailer-after-EOI is OK)
    - PNG: Valid PNG signature + IEND in the tail
    - TIFF: Valid TIFF header + readable last pixel
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

        # Beside-final temps end in ".tmp" (item 26), but this check must
        # still verify them — a truncated temp is exactly what it exists to
        # catch. So the branch is chosen by CONTENT where a signature is
        # unambiguous, and falls back to the extension for everything else
        # (TIFF, or a bare ".tmp" of unknown kind, which then refuses — no
        # false pass for garbage).
        _is_jxl = header == b'\x00\x00\x00\x0cJXL \r\n\x87\n'
        _is_jpeg = header[0:2] == b'\xff\xd8' or ext in ('.jpg', '.jpeg', '.jfif', '.jpe')
        _is_png = header[0:8] == b'\x89PNG\r\n\x1a\n' or ext == '.png'
        _is_tiff = (ext in ('.tif', '.tiff')
                    and not (_is_jxl or _is_jpeg or _is_png))

        if _is_jxl:
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
            # A codestream split into jxlp boxes must be WHOLE: indices count
            # up from 0 and the last box has the high bit set (ISO/IEC
            # 18181-2) — a file cut at a box boundary only fails this check.
            # Same rule as the encoder/recompressor _verify_jxl_integrity.
            file_size = stat.st_size
            i = 12
            has_codestream = False
            has_jxlc = False
            jxlp_next = 0
            jxlp_ended = False
            # Box walk re-opens the file only on the delete-gate path (JXL).
            with open(file_path, 'rb') as f:
                while i < file_size:
                    if i + 8 > file_size:
                        return False
                    f.seek(i)
                    box_header = f.read(8)
                    size = int.from_bytes(box_header[0:4], "big")
                    box_type = box_header[4:8]
                    if box_type in (b"jxlc", b"jxlp"):
                        has_codestream = True
                    head = 8
                    if size == 1:
                        if i + 16 > file_size:
                            return False
                        size = int.from_bytes(f.read(8), "big")
                        if size < 16:
                            return False
                        head = 16
                    elif size != 0 and size < 8:
                        return False
                    if size != 0 and i + size > file_size:
                        return False
                    if box_type == b"jxlc":
                        has_jxlc = True
                    elif box_type == b"jxlp":
                        end = file_size if size == 0 else i + size
                        if jxlp_ended or end - (i + head) < 4:
                            return False
                        f.seek(i + head)
                        index = int.from_bytes(f.read(4), "big")
                        if (index & 0x7FFFFFFF) != jxlp_next:
                            return False
                        jxlp_next += 1
                        jxlp_ended = bool(index & 0x80000000)
                    if size == 0:
                        i = file_size
                        break
                    i += size
            if jxlp_next and (has_jxlc or not jxlp_ended):
                return False
            return has_codestream and i == file_size
        
        elif _is_jpeg:
            # SOI at the start AND an EOI (0xFFD9) at the LOGICAL end of the
            # JPEG — a truncated or short-written JPEG must never pass the
            # delete gate. The EOI does NOT have to be the last two bytes:
            # jbrd bit-exact reconstruction preserves trailing data after the
            # EOI (Motion Photos, appended thumbnails, scanner payloads), so
            # the scan walks BACKWARD from EOF until it finds one. The old
            # fixed 64 KiB tail window rejected exactly the files the comment
            # above names — a 200 KB Motion Photo trailer pushed the EOI out
            # of the window, the encode and its bit-exact reconstruction were
            # perfect, yet this check refused both the JPEG and the recovered
            # copy of it (2026-09-21 audit, item 2). Trailer-after-EOI is
            # legitimate; no EOI anywhere is not.
            #
            # "An EOI anywhere" was not enough, though (T2 of the 2026-10-08
            # audit): the EXIF thumbnail inside APP1 is a whole JPEG with its
            # own EOI near the START of the file, so a real camera JPEG cut in
            # half still passed. The EOI must be the one that closes the MAIN
            # image: _jpeg_main_eoi walks the segment chain from the SOI
            # (APPn and friends skipped by their length, entropy-coded data
            # scanned for its terminating marker).
            if header[0:2] != b'\xff\xd8':
                return False
            return _jpeg_main_eoi(file_path)

        elif _is_png:
            # PNG signature AND the IEND chunk closing the stream (search the
            # tail: PNGs with appended data are valid too).
            if header[0:8] != b'\x89PNG\r\n\x1a\n':
                return False
            if stat.st_size < 20:
                return False
            with open(file_path, 'rb') as f:
                f.seek(max(0, stat.st_size - 65536))
                return b'IEND' in f.read()

        elif _is_tiff:
            # TIFF: signature, then force a real read of the last page's last
            # pixel — tifffile is lazy and a truncated file can pass a
            # header-only check.
            # Classic (42) or BigTIFF (43), in the byte order the first two
            # bytes name (#585).
            if (header[0:2], header[2:4]) not in {
                    (b"II", b"\x2a\x00"), (b"II", b"\x2b\x00"),
                    (b"MM", b"\x00\x2a"), (b"MM", b"\x00\x2b")}:
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


_validate_export_folder_name = _common._validate_export_folder_name


_marker_matches = _common._marker_matches


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

COMPRESS_METADATA_BOXES = False
# How the EXIF and XMP copied from a JPEG are stored in the JXL.
# False (default) — plain boxes: IrfanView (and any reader without Brotli)
#   shows the EXIF. They take the same room they took in the JPEG.
# True — Brotli-compressed (brob) boxes, cjxl's own default: a few KB smaller
#   per photo (see docs/behavior_and_limitations.md for measured numbers),
#   but IrfanView then shows no EXIF at all, and --repair-jbrd leaves the
#   EXIF layout alone. The rebuilt JPEG is byte-identical either way.
# Can also be set via --compress-metadata.

JPEG_ORIENTATION = "original"
# [JXL -> JPEG] How a JXL whose photo was a ROTATED JPEG (EXIF Orientation
# 2-8) comes back as a JPEG.
# "original" (default) — as the camera wrote it: the pixels in their stored
#   order and the original EXIF Orientation (6, 8, ...). Needs ImageMagick;
#   without it the JPEG comes out upright, with a warning.
# "upright" — the pixels turned upright and EXIF Orientation 1 (what v2.25.3
#   wrote).
# A PNG always comes out upright (many viewers ignore a PNG's orientation),
# and so does the decoder's TIFF. The bit-exact recovery of a JPEG archive is
# not affected: it returns the original file. Also: --jpeg-orientation.

AUTO_REPAIR_JBRD = False
# [JXL -> JPEG lossless decode, djxl >= 0.12]
# True — when djxl --reconstruct_jpeg fails on a jbrd JXL (the v2.0.0-v2.0.3
#   XMP-marker damage), repair a COPY of the file (strip the toolkit markers,
#   retest) and decode the JPEG from that copy. The JXL on disk is NEVER
#   modified: healing the archive itself is --repair-jbrd's job. The recovered
#   JPEG has identical image data but re-serialized XMP bytes, so it is NOT
#   bit-identical to the original JPEG — and the delete gate never deletes
#   that source JXL in the same run.
# False (default) — the file is reported as an error, with a hint pointing at
#   both remedies.
# Can also be set via --auto-repair-jbrd.

STORE_MD5 = True
# Store MD5 checksums during transcode encode (for verify during decode)

PROVENANCE_CHECK = "path"
# How an EXISTING output is matched to the source about to replace it.
# With DELETE_SOURCE in modes 2/4/5/6/7 (which drop folder structure, so two
# sources in different folders resolve to the same output) EVERY existing
# output must prove it came from this source; without this a second run
# overwrote the first archive and deleted both originals. Every other
# --sync/--overwrite run still refuses to overwrite an output that is
# provably another source's (see _foreign_overwrite_filter): the overwrite is
# the loss when an earlier run already deleted that other source (#268).
# "path" (default) compares the recorded LOCATION; "content" also accepts
# matching source bytes, so it survives MOVED folders. See _provenance_ok.
#
# NOTE: the JXL -> JPEG LOSSLESS path writes no marker. Its output has to
# stay byte-identical to the original JPEG, and injecting XMP would break
# exactly the promise that path exists to keep. It uses checksums.md5
# instead: the stored hash IS the original JPEG, so an existing output that
# matches it came from this JXL.

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
#   * LOSSY convert (JXL -> JPEG/PNG, or PNG/JPEG -> JXL lossy) — nothing can
#     be re-derived, but every lossy output this script writes carries the
#     jxlphoto-src/srcsum provenance marker of its source. The gate requires
#     that marker to MATCH (the encoder's E-1 rule), plus the structural check:
#     a same-named file from anywhere else (a camera JPEG next to its archived
#     master, say) never certifies a deletion. Markerless outputs (written
#     before the markers, or by another tool) keep their source.

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

EXIFTOOL_TIMEOUT = CODEC_TIMEOUT
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
# Staging directory for output files during conversion
# Example: r"E:\staging_jxl"

# ImageMagick detection (auto, do not modify)
MAGICK_AVAILABLE = shutil.which("magick") is not None

# ExifTool detection - try both name variants
_exiftool_cmd = None

_marker_anchor_index = _common._marker_anchor_index

def _get_exiftool_cmd(*a, **k): return _common._get_exiftool_cmd(_G, *a, **k)

_tool_version = _common._tool_version

_tool_at_least = _common._tool_at_least

def _cjxl_buffering_flag():
    """--buffering flag for cjxl >= 0.12; empty list otherwise (flag doesn't exist there).

    Probes the bare "cjxl" while jxl_tiff_encoder.py probes _get_cjxl_cmd():
    NOT drift. This script invokes the bare "cjxl" everywhere (there is no
    configurable path here), so it must version-check the binary it actually
    runs. Pointing this at an accessor that does not exist in this module would
    be the bug, not the fix — deliberately kept out of SHARED_HELPERS.
    """
    if CJXL_BUFFERING is not None and _tool_at_least("cjxl", 0, 12):
        return [f"--buffering={CJXL_BUFFERING}"]
    return []

# cjxl Brotli-compresses the Exif/xml boxes it copies from a JPEG (brob
# boxes) unless told not to, and IrfanView then shows no EXIF at all (#562).
# Plain boxes cost the few KB the JPEG itself spent on them; a JPEG rebuilt
# with --reconstruct_jpeg is byte-identical either way. libjxl 0.7+.
_PLAIN_BOXES_FLAG = ["--compress_boxes=0"]


def _metadata_boxes_flag():
    """cjxl's --compress_boxes as COMPRESS_METADATA_BOXES asks."""
    return ["--compress_boxes=1"] if COMPRESS_METADATA_BOXES else _PLAIN_BOXES_FLAG

# #639: cjxl without the EXIF box it would copy from the JPEG (the retry of
# encode_to_jxl when that copy is malformed and exiftool cannot rewrite it).
_CJXL_STRIP_EXIF = ["-x", "strip=exif"]

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

_argfile_safe = _common._argfile_safe


_struct_escape = _common._struct_escape


_history_mime = _common._history_mime


def _history_args(*a, **k): return _common._history_args(_G, *a, **k)


def _guard_refusal_hint(*a, **k): return _common._guard_refusal_hint(_G, *a, **k)


# Charset directives for exiftool argfiles:
# - FileName=UTF8: file paths in the argfile are UTF-8.
# - UTF8: tag VALUES read/written are UTF-8 (non-ASCII metadata round-trips
#   without codepage corruption).
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


def _run_captured(*a, **k): return _common._run_captured(_G, *a, **k)


_stderr_tail = _common._stderr_tail


def _exiftool_source(*a, **k): return _common._exiftool_source(_G, *a, **k)


def _run_exiftool_argfile(*a, **k): return _common._run_exiftool_argfile(_G, *a, **k)


SRC_PREFIX = "jxlphoto-src:"
SRCSUM_PREFIX = "jxlphoto-srcsum:"
# Must match the encoder's SRC_XMP_PREFIX / SRCSUM_XMP_PREFIX: they record
# WHICH source made an output, so a later run can refuse to overwrite one
# archive with a different file that happens to share its name.
ICC_PROFILE_PREFIX = _common.ICC_PROFILE_PREFIX
# Must match the encoder's: the ORIGINAL ICC profile in dc:Relation as
# "jxlphoto-iccprofile:<base64>" (XMP CreatorTool "ICC:<base64>" in JXLs
# written up to v2.18.x). Read as the source profile of a decode; never left
# in a delivered JPEG/PNG, whose profile is in the normal place.
TIFFTAG_PREFIX = "jxlphoto-tifftag:"
# Must match the encoder's TIFFTAG_XMP_PREFIX: a TIFF tag of the master's
# source that only a JXL -> TIFF decode writes back (the IPTC and Photoshop
# blocks, a scanner's private tags), as hex. Never left in a delivered
# JPEG/PNG (v2.28.0): nothing reads it there.
_GAIN_MAP_XMP_REMOVAL = ["-XMP-hdrgm:all=", "-XMP-GContainer:all="]
# The XMP that announces an HDR gain map (Android's Ultra HDR): removed from
# every output of the convert path, which never holds the gain map itself.
ORIGIN_PREFIX = _common.ORIGIN_PREFIX
# dc:Relation token naming what the FIRST master of this photo was made from:
# tiff8 / tiff16 (encoder), jpeg / png8 / png16 / png (transcoder, pixel
# re-encode), jpeg (recompressor re-encoding a jbrd JXL), jxl-lossy /
# jxl-lossless (recompressor, a JXL no toolkit script wrote, as jxlinfo reads
# its header; plain `jxl` when jxlinfo could not tell, and in files written by
# v2.10.0). Written once by the script that first touches a clean input, then
# carried forward unchanged — absent means unknown, never guessed. The only
# reader is the recompressor's --on-regeneration guard (_hidden_generations).
ORIENTATION_PREFIX = "jxlphoto-orientation:"
# dc:Relation token: the EXIF Orientation (2-8) of the JPEG this photo came
# from, written by the recompressor when its re-encode turns the pixels
# upright (the codestream then says 1). Read here to give a JPEG its original
# orientation back (JPEG_ORIENTATION); never left in a delivered JPEG/PNG.
_TOOLKIT_MARKER_NAMESPACE = _common._TOOLKIT_MARKER_NAMESPACE
# An encode record anywhere in Description/Software: the input came from this
# toolkit (an older version, before the origin marker existed).
_ORIGIN_RECORD_RE = re.compile(r"(?:^|\|)\s*cjxl d=", re.MULTILINE)


# --- Provenance: which source made this output -----------------------------
# The code of this section has ONE copy, in jxl_photo_common.py (AGENTS.md,
# "The common module"); the names below bind it here. Fix it there.
#
# The modes that COLLAPSE folder structure (2/4/5/6/7) drop the source's folder
# from the output path, so two sources in different folders resolve to the same
# output. _abort_on_duplicate_outputs catches that inside one run; only a
# recorded marker catches it ACROSS runs — and with --delete-source the second
# run would otherwise overwrite the first archive and delete both originals.
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


def _read_derived_markers_batch(*a, **k): return _common._read_derived_markers_batch(_G, *a, **k)


def _read_all_source_marker_values(jxl_path: Path):
    """EVERY jxlphoto-src / jxlphoto-srcsum VALUE in dc:Relation, in file order.

    `_read_source_markers_batch` keeps only the LAST value of each (all that
    provenance matching ever needs), so it cannot tell --repair-jbrd how many
    marker pairs a damaged file carries. A file written by v2.0.0-v2.0.3 through
    more than one pass can hold several ids; stripping only the last left the
    reconstruction broken and the file reported STILL BROKEN though it was
    repairable. Returns (src_values, srcsum_values); a read failure comes back
    empty (the caller then falls back to whatever it already read, and the
    post-repair reconstruction test is the real proof).
    """
    srcs, srcsums = [], []
    try:
        r = _run_exiftool_argfile(["-j", "-s", "-s", "-XMP-dc:Relation",
                                   str(jxl_path)], timeout=EXIFTOOL_TIMEOUT)
        data = json.loads(r.stdout) if r.stdout else []
    except Exception:
        return [], []
    rel = None
    for entry in data:
        if isinstance(entry, dict) and "Relation" in entry:
            rel = entry["Relation"]
            break
    if rel is None:
        return [], []
    values = rel if isinstance(rel, list) else [str(rel)]
    for token in values:
        token = str(token).strip()
        if token.startswith(SRC_PREFIX):
            srcs.append(token[len(SRC_PREFIX):])
        elif token.startswith(SRCSUM_PREFIX):
            srcsums.append(token[len(SRCSUM_PREFIX):])
    return srcs, srcsums


def _inherited_marker_removal_lines(out_path: Path) -> list:
    """'-XMP-dc:Relation-=' lines for every jxlphoto-src/srcsum value that
    _copy_metadata carried over from the SOURCE into `out_path`.

    Goes in the SAME exiftool call that writes this output's own pair
    (_provenance_marker_args): exiftool applies -= and += on the bag together,
    so the output ends with exactly one pair — its own — or, if the call fails,
    exactly as before. A JPEG decoded from a master used to carry the master's
    pair (naming the TIFF) next to its own (naming the JXL); every reader keeps
    the LAST value, so no gate erred, but one that kept the first would have
    let a decode prove the TIFF archived. A read failure returns [] (the old
    two-pair behaviour, still correct for every reader)."""
    srcs, srcsums = _read_all_source_marker_values(out_path)
    lines = []
    for value in dict.fromkeys(srcs):
        lines.append("-XMP-dc:Relation-=" + SRC_PREFIX + _argfile_safe(value))
    for value in dict.fromkeys(srcsums):
        lines.append("-XMP-dc:Relation-=" + SRCSUM_PREFIX + _argfile_safe(value))
    return lines


def _provenance_ok(*a, **k): return _common._provenance_ok(_G, *a, **k)


def _provenance_proves_archive(*a, **k): return _common._provenance_proves_archive(_G, *a, **k)


def _provenance_marker_args(*a, **k): return _common._provenance_marker_args(_G, *a, **k)


def _read_origin_inputs(*a, **k): return _common._read_origin_inputs(_G, *a, **k)


_origin_for_output = _common._origin_for_output


def _copy_metadata(src_path: Path, dst_path: Path) -> None:
    """Best-effort copy of EXIF/XMP/IPTC metadata using exiftool.

    Used for lossy convert paths where cjxl/djxl may not preserve metadata.
    Raises RuntimeError when exiftool fails (#571): the caller turns that into
    an ERROR for the file, so a --delete-source run keeps the source instead
    of deleting it behind an output that lost its IPTC or its markers — the
    verdict the encoder and the decoder already give. "No writable tags"
    (a source without that metadata) is exit 0, a success.
    Also strips the ICC copy the TIFF encoder writes for TIFF round-trips —
    the jxlphoto-iccprofile token in dc:Relation, and the 'ICC:<base64>' blob
    in XMP CreatorTool of files written up to v2.18.x: it bloats delivered
    JPEG/PNGs and, after an ICC conversion, points at the wrong profile.
    """
    if shutil.which(_get_exiftool_cmd()) is None:
        return
    # #623: out of a JXL whose codestream carries an orientation, the pixels
    # are already turned; the copied EXIF Orientation is reset to 1.
    _reset = (_orientation_reset_args(src_path)
              if src_path.suffix.lower() in JXL_EXTS else [])
    try:
        # A '%' in the source's name is expanded by -tagsfromfile and the
        # output got no metadata (#550): copy from a container instead.
        with tempfile.TemporaryDirectory(prefix="jxltr_meta_", dir=TEMP_DIR) as _mt:
            r_copy = _run_exiftool_argfile(
                ["-overwrite_original", "-tagsfromfile", _exiftool_source(src_path, _mt),
                 # ExifByteOrder: the output's EXIF keeps the source's II/MM
                 # (a new EXIF block is big-endian by default).
                 "-exif:all", "-ExifByteOrder<ExifByteOrder",
                 # #637: exiftool's "unsafe" tags are copied only when named:
                 # InteropIndex says which DCF colour space the camera used
                 # (R98 sRGB, R03 Adobe RGB). decode_to_image clears them
                 # again on a colour-converted output.
                 "-InteropIFD:InteropIndex", "-InteropIFD:InteropVersion",
                 "-xmp:all", "-iptc:all"]
                # No output of this path holds an HDR gain map (#641): the
                # XMP that announces one (Ultra HDR's hdrgm:Version and the
                # container directory listing a "GainMap" item of N bytes)
                # must not describe a file that has none.
                + _GAIN_MAP_XMP_REMOVAL
                + _reset + [str(dst_path)],
                timeout=EXIFTOOL_TIMEOUT
            )
        if r_copy.returncode != 0:
            raise RuntimeError(f"metadata copy failed (exiftool rc={r_copy.returncode}): "
                               f"{(r_copy.stderr or r_copy.stdout or '').strip()[:200]}")
        # Strip the ICC copy: jxlphoto-iccprofile tokens from dc:Relation,
        # ICC:<base64> segments from CreatorTool (same logic as the TIFF
        # decoder's cleanup_xmp_icc) — one read, one write. A read that
        # fails raises: the token would stay in the delivered file.
        content, _toks = _read_creator_and_relation(dst_path)
        # A JPEG/PNG also drops the TIFF tags a master carries for the
        # decoder (jxlphoto-tifftag: the IPTC and Photoshop blocks, a
        # scanner's private tags): only a JXL -> TIFF decode reads them, and
        # they are up to 8 KB of hex per tag in a file meant to be light.
        _drop = (ICC_PROFILE_PREFIX, ORIENTATION_PREFIX)
        if src_path.suffix.lower() in JXL_EXTS:
            _drop += (TIFFTAG_PREFIX,)
        strip = ["-XMP-dc:Relation-=" + _argfile_safe(t)
                 for t in dict.fromkeys(_toks)
                 if t.startswith(_drop)]
        if "ICC:" in content:
            content = content.strip()
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
            # _argfile_safe like every other CreatorTool write in the
            # toolkit: an argfile is one argument per LINE, so a
            # multi-line CreatorTool copied from another program would
            # split into several bogus arguments here.
            strip.append(f"-XMP-xmp:CreatorTool={_argfile_safe(clean)}")
        if strip:
            r_strip = _run_exiftool_argfile(
                ["-overwrite_original"] + strip + [str(dst_path)],
                timeout=EXIFTOOL_TIMEOUT
            )
            if r_strip.returncode != 0:
                raise RuntimeError(
                    f"ICC-copy cleanup failed (exiftool rc={r_strip.returncode}): "
                    f"{(r_strip.stderr or r_strip.stdout or '').strip()[:200]}")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"metadata copy failed: {e}") from e

def _write_converted_metadata(src_path: Path, write_path: Path) -> None:
    """The metadata of a lossy JPEG/PNG -> JXL: the copy from the source, then
    the provenance, origin and History lines (never into a jbrd container: the
    caller checks). RuntimeError when exiftool fails either call (#571)."""
    _copy_metadata(src_path, write_path)
    r_prov = _run_exiftool_argfile(
        ["-overwrite_original"] + _inherited_marker_removal_lines(write_path)
        + _provenance_marker_args(src_path)
        + _origin_marker_lines(src_path)
        + _history_args("converted", src_path, write_path)
        + [str(write_path)], timeout=EXIFTOOL_TIMEOUT)
    if r_prov.returncode != 0:
        raise RuntimeError(
            f"provenance/history write failed (exiftool rc={r_prov.returncode}): "
            f"{(r_prov.stderr or r_prov.stdout or '').strip()[:200]}")


def _exif_tag_count(path: Path):
    """How many EXIF tags exiftool reads in `path`; None when it cannot read it."""
    r = _run_exiftool_argfile(["-j", "-EXIF:all", str(path)], timeout=EXIFTOOL_TIMEOUT)
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return None
    entry = data[0] if data else {}
    return len([k for k in entry if k != "SourceFile"])


def _verify_exif_kept(src_path: Path, out_path: Path) -> None:
    """Fail closed after the #639 retry: a source that has EXIF must leave a
    JXL that has some. An output that lost it all is `METADATA PARTIAL`, an
    error for the file, so --delete-source can never remove a JPEG whose EXIF
    did not arrive. Cannot read either file = cannot tell = error."""
    n_src = _exif_tag_count(src_path)
    n_out = _exif_tag_count(out_path)
    if n_src is None or n_out is None:
        raise RuntimeError("METADATA PARTIAL: could not check that the EXIF reached the JXL")
    if n_src and not n_out:
        raise RuntimeError(f"METADATA PARTIAL: the JPEG's EXIF ({n_src} tags) did not reach the JXL")


def _with_makernote_hint(message: str) -> str:
    """`message`, plus how to get such a JPEG into a JXL when the failure is
    the maker notes exiftool cannot parse: once they are in the output it
    refuses every write (#639). The lossless transcode writes no metadata.
    exiftool words it in several ways ("Maker notes could not be parsed",
    "Bad MakerNotes offset for ...", "Overlapping MakerNotes values"): any
    failure that names the maker notes gets the hint."""
    if re.search(r"maker ?notes", message, re.IGNORECASE):
        return (message + " | its EXIF carries maker notes exiftool cannot parse; the "
                "lossy conversion cannot add its markers to it. --force-transcode "
                "(lossless, bit-exact) keeps the JPEG's EXIF as it is")
    return message


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
    "PNG_DEFAULT_BIT_DEPTH": ("choice", 8, 16),
    "JPEG_DEFAULT_QUALITY": ("int", 1, 100),
    "CJXL_EFFORT": ("int", 1, 10),
    "CJXL_BUFFERING": ("choice", None, 0, 1, 2, 3),
    "COMPRESS_METADATA_BOXES": ("bool",),
    "JPEG_ORIENTATION": ("choice", "original", "upright"),
    "CODEC_TIMEOUT": ("int", 1, None),
    "EXIFTOOL_TIMEOUT": ("int", 1, None),
    "EXIFTOOL_BATCH_STALL": ("int", 1, None),
    "EXIFTOOL_BATCH_TIMEOUT": ("int", 1, None),
    "TEMP_DIR": ("path?",),
    "TEMP2_DIR": ("path?",),
    "CONVERTED_JXL_FOLDER": ("name",),
    "RECOVERED_JPEG_FOLDER": ("name",),
    "JXL_SIBLING_FOLDER": ("name",),
    "JPEG_SIBLING_FOLDER": ("name",),
    "JPEG_SUFFIX_TO_REPLACE": ("name",),
    "JXL_SUFFIX_REPLACE": ("name",),
    "JXL_SUFFIX_TO_REPLACE": ("name",),
    "JPEG_SUFFIX_REPLACE_DEC": ("name",),
    "EXPORT_MARKER": ("name",),
    "EXPORT_JXL_FOLDER": ("name",),
    "EXPORT_JPEG_FOLDER": ("name",),
    "EXPORT_JPEG_SUBFOLDER": ("name?",),
    "CONVERT_OUTPUT_FOLDER": ("name",),
    "CONVERT_OUTPUT_SUFFIX": ("name?",),
    "FORCE_CONTAINER_FOR_LOSSY": ("bool",),
}
# Settings computed from another one: they follow it (see _apply_settings_overlay).
_OVERLAY_DERIVED = {"EXIFTOOL_TIMEOUT": "CODEC_TIMEOUT"}
_SETTINGS_OVERLAY = _apply_settings_overlay(globals(), "jxl_jpeg_transcoder", _OVERLAY_SETTINGS,
                                            _OVERLAY_DERIVED)

SCRIPT_DIR = Path(__file__).parent
# JXLPHOTO_LOG_DIR (environment variable) moves the log folders elsewhere —
# the test suite and the real-photo battery set it so their runs never
# land in Logs\ next to the scripts.
LOG_DIR = (Path(os.environ["JXLPHOTO_LOG_DIR"]) if os.environ.get("JXLPHOTO_LOG_DIR")
           else SCRIPT_DIR / "Logs") / Path(__file__).stem
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

# Sources (str paths) whose decode this run succeeded from an AUTO_REPAIR_JBRD
# copy, not from the JXL itself. The delete gate reads it to fail closed: that
# run never proved THIS JXL can recover the original JPEG. Workers only add;
# the gate runs after every future has completed.
_auto_repaired = set()

# --resize-*/--sharpen runtime state, reset at the top of main() (the test
# suite runs several commands in one process). Only the JXL -> JPEG/PNG decode
# direction honors them; the encode direction ignores them with a warning.
RESIZE_MODE = None           # "long" / "short" / "percent" / None
RESIZE_VALUE = None
ALLOW_UPSCALE = False
SHARPEN = "none"             # none / screen / print
LABEL_QUALITY = None         # JPEG quality in the label (/q<N>): JPEG derivatives only
SHARPEN_SIGMA = None         # expert overrides (deliberately OUTSIDE the label)
SHARPEN_GAIN = None
SHARPEN_THRESHOLD = None
# Written into dc:Relation on every resized/sharpened output: it marks the file
# as a DERIVATIVE — never an archive of its source.
DERIVED_XMP_PREFIX = "jxlphoto-derived:"
# The encoder's THUMB_XMP_FLAG: the JXL is the THUMBNAIL page of a split TIFF
# (the reduced preview it carried). A derivative is never made of one (#555).
THUMB_FLAG = "jxlphoto-thumb"
PAGE_PREFIX = _common.PAGE_PREFIX
# The encoder's PAGE_XMP_PREFIX: which page of its TIFF this JXL holds.
_CAMERA_THUMB_MAX_SIDE = _common._CAMERA_THUMB_MAX_SIDE


_magick_out_path = _common._magick_out_path


_is_camera_thumbnail_page = _common._is_camera_thumbnail_page


# Final paths (normcase) whose existing jxlphoto-derived label names another
# recipe: the skip logic must re-derive them instead of keeping them.
_FORCE_REDERIVE = set()

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
    # PID suffix: two children of the same script started in the same second
    # (two manifest entries) would otherwise open the SAME file in append;
    # the counter covers a same-process repeat inside that second.
    n = 0
    while True:
        log_file = LOG_DIR / f"{timestamp}_{os.getpid()}{('_' + str(n)) if n else ''}.log"
        if not log_file.exists():
            break
        n += 1

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
    logger.info(f"{TOOLKIT_NAME} {TOOLKIT_VERSION} | {Path(__file__).name}")
    _log_settings_overlay()
    # (No delete summary here: setup_logger runs BEFORE anything is deleted, so
    # calling it printed "Sources DELETED: 0 | kept by a gate: 0" at the top of
    # every archive run — a line that reads like a result and is only ever
    # zeros. The real one is logged by each cmd_* at the end.)
    logger.info(f"Log: {log_file}")
    return log_file


_rejected_log_lock = threading.Lock()


def _log_rejected_file(*a, **k): return _common._log_rejected_file(_G, *a, **k)


def next_count(*a, **k): return _common.next_count(_G, *a, **k)

# --------------------------------------------─
# MD5 UTILITIES (Transcode only)
# --------------------------------------------─

md5_of_file = _common.md5_of_file

CHECKSUMS_FILENAME = "checksums.md5"

_MD5_LOCK_TIMEOUT_S = 30.0
_MD5_LOCK_STALE_S = 120.0
_MD5_LOCK_RELEASE_S = 5.0


def _release_checksum_lock(lock_path: Path) -> None:
    """Remove the lock file, retrying a refused delete (#612).

    Windows refuses a delete while another program (an antivirus scanning
    the file it just saw created) holds it open. The one-shot unlink this
    replaces swallowed that error: the lock outlived its writer, every other
    worker of the folder waited the whole timeout and dropped its provenance
    line (fail closed — the JPEGs were kept, twelve at once in the real-photo
    battery of 2026-11-05) until the lock was old enough to be reclaimed."""
    deadline = time.monotonic() + _MD5_LOCK_RELEASE_S
    while True:
        try:
            os.unlink(lock_path)
            return
        except FileNotFoundError:
            return
        except OSError as e:
            if time.monotonic() >= deadline:
                logger.warning(f"Could not remove {lock_path} ({e}) — other writers of "
                               f"this folder wait until it is {_MD5_LOCK_STALE_S:.0f}s old")
                return
            time.sleep(0.05)

def _append_checksum_line(db_path: Path, entry: str):
    """Append one line to checksums.md5, serialized ACROSS PROCESSES.

    _md5_db_lock (thread lock) only serializes this process; two manifest
    entries targeting the same folder are separate child processes, and their
    appends used to interleave mid-line, corrupting the db. The lock is a
    sibling `<db>.lock` file created O_EXCL with backoff. Fail-CLOSED on every
    path: a lock that cannot be taken means the line is NOT written (a missing
    entry reads as "no provenance recorded", which blocks deletions) — never a
    possibly-corrupt write. A lock older than _MD5_LOCK_STALE_S is from a
    crashed process and is reclaimed.
    """
    lock_path = db_path.parent / (db_path.name + ".lock")
    deadline = time.monotonic() + _MD5_LOCK_TIMEOUT_S
    last_err = None
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except (FileExistsError, PermissionError) as e:
            # Windows answers a create on a lock file another writer is still
            # deleting ("delete pending") with ACCESS DENIED, not "exists": the
            # lock is simply held, so wait for it like any other (#547). Two
            # workers finishing in the same folder hit it; the line was then
            # dropped and the source kept although its JXL was fine.
            last_err = e
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0  # vanished between open and stat: retry immediately
            if age > _MD5_LOCK_STALE_S:
                try:
                    lock_path.unlink()
                    continue
                except OSError:
                    pass  # someone else reclaimed it first
            if time.monotonic() >= deadline:
                if isinstance(last_err, PermissionError):
                    logger.warning(f"Could not lock {lock_path} ({last_err}) — provenance "
                                   f"line NOT recorded (fail closed)")
                else:
                    logger.warning(f"checksums.md5 stayed locked for "
                                   f"{_MD5_LOCK_TIMEOUT_S:.0f}s — provenance line for "
                                   f"{db_path.parent.name} NOT recorded (fail closed)")
                return False
            time.sleep(0.05)
        except OSError as e:
            logger.warning(f"Could not lock {lock_path} ({e}) — provenance line "
                           f"NOT recorded (fail closed)")
            return False
    try:
        with _md5_db_lock:
            with open(db_path, "a", encoding="utf-8") as f:
                f.write(entry)
        return True
    finally:
        _release_checksum_lock(lock_path)

def store_md5_db(jxl_path: Path, md5: str):
    db_path = jxl_path.parent / CHECKSUMS_FILENAME
    entry = f"{md5}  {jxl_path.name}\n"
    return _append_checksum_line(db_path, entry)

def read_md5_db(jxl_path: Path) -> Optional[str]:
    db_path = jxl_path.parent / CHECKSUMS_FILENAME
    if not db_path.exists():
        return None
    target = jxl_path.name
    # The db is a plain text file that can be torn (crash mid-append), locked
    # by an AV scanner, or saved by Notepad with a BOM. Any read failure must
    # return None — "no provenance recorded" — because a caller either uses it
    # as a delete proof (None = fail-closed KEEP) or to REFUSE overwriting
    # (None = refuse). utf-8-sig swallows the BOM the plain utf-8 read turned
    # into a perpetual mismatch (2026-09-21 audit, item 23).
    try:
        with open(db_path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
    except Exception:
        return None
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

# The JXL's OWN md5 is stored under the plain output name plus this suffix
# (e.g. "photo.jxl.jxl-md5"), alongside the "<source md5>  photo.jxl" line.
# read_md5_db matches names EXACTLY, so the suffixed key can never shadow or
# be returned by a plain-name lookup: databases written before this key
# existed (no self-hash line) and databases read by older versions (which
# never ask for the suffixed key) both keep working unchanged.
JXL_SELF_HASH_SUFFIX = ".jxl-md5"

def store_jxl_self_hash_db(jxl_path: Path, md5: str):
    db_path = jxl_path.parent / CHECKSUMS_FILENAME
    entry = f"{md5}  {jxl_path.name}{JXL_SELF_HASH_SUFFIX}\n"
    return _append_checksum_line(db_path, entry)

def _file_checksum_lines(jxl_path: Path, src_md5: str, jxl_md5: Optional[str]) -> bool:
    """An output's lines (source md5, JXL self-hash) in its own folder's db.

    False, with a warning, when the db cannot be written (read-only, held open
    by a scanner): the run goes on, the output simply has no line, which every
    gate reads as "not proven" (fail closed), and the next deleting run
    settles it by reconstruction (#556)."""
    try:
        # A lock that timed out returns False (#579): reporting it as filed
        # skipped the retry at the end of the run. `is False`, not falsiness:
        # None is a write that raised nothing.
        if store_md5_db(jxl_path, src_md5) is False:
            return False
        if jxl_md5 and store_jxl_self_hash_db(jxl_path, jxl_md5) is False:
            return False
        return True
    except OSError as e:
        logger.warning(f"  checksum of {jxl_path.name} NOT recorded ({e}) — fail closed")
        return False


def read_jxl_self_hash_db(jxl_path: Path) -> Optional[str]:
    db_path = jxl_path.parent / CHECKSUMS_FILENAME
    if not db_path.exists():
        return None
    target = jxl_path.name + JXL_SELF_HASH_SUFFIX
    # Same hardening as read_md5_db: torn/locked/non-UTF8 db -> None, which
    # makes the caller fall back to the reconstruction proof (or refuse) —
    # never crash the run and never trust a half-read db (item 23).
    try:
        with open(db_path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
    except Exception:
        return None
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

has_jbrd_box = _common.has_jbrd_box

_exif_box_rank = _common._exif_box_rank


reorder_jxl_boxes = _common.reorder_jxl_boxes

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

def resolve_output_transcode(src_path: Path, mode: int, input_root: Path, decode: bool,
                             single_file: bool = False) -> Path:
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
        # Modes 4/5 accept a single FILE as input: testing the output against
        # the file itself can never pass (a path is never "under" a file), and
        # testing against the file's parent still flags EVERY legitimate
        # single-file mode-4/5 run: those modes write a SIBLING of the file's
        # folder by design. The tree such a run must stay inside is the folder
        # holding that sibling pair. The single_file flag is explicit because
        # the callers disagree on what input_root means: cmd_transcode and
        # cmd_convert pass the file's PARENT here, cmd_auto passes the FILE —
        # an is_file() inference alone fired for one and never for the other
        # (2026-09-21 audit, item 29; recompressor pattern).
        if single_file:
            anchor = input_root.parent.parent if input_root.is_file() else input_root.parent
        else:
            anchor = input_root
        if result is not None and not _is_relative_to(result, anchor):
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
        export_idx = _marker_anchor_index(parts[:-1], input_root, marker_lower)
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
    jxl_md5 = None
    # Never write under the final name without staging: a run killed
    # externally (idle-timeout kill, Ctrl+C, power loss) would leave a
    # TRUNCATED file at the final path with a NEW mtime, which the next
    # skip-existing/smart-sync run then treats as done forever. Write to a
    # uuid temp BESIDE the final and swap it in with an atomic same-folder
    # os.replace only after the integrity check passed.
    #
    # The temp ends in ".tmp", NOT the final extension (the v2.1.1
    # repair-jbrd rule: ".tmp, not .jxl"): a run killed between the write
    # and the os.replace below would otherwise leave an adoptable
    # "<uuid>_<name>.jxl" beside the final — this folder is scanned again
    # on the next run, and the orphan would be picked up as real input
    # (2026-09-21 audit, item 26). os.replace does not care about the
    # extension, and cjxl picks the JXL writer for an unknown extension
    # (verified against cjxl 0.12.0).
    _promote_local = write_path == final_path
    if _promote_local:
        write_path = final_path.parent / f"{uuid.uuid4().hex}_{final_path.name}.tmp"
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
        # Plain Exif/xml boxes unless COMPRESS_METADATA_BOXES: IrfanView reads
        # no EXIF from a Brotli-compressed (brob) box, the cjxl default (#562);
        # the reconstructed JPEG is the same either way.
        r = _run_captured(
            ["cjxl", str(src_path), str(write_path), "--lossless_jpeg=1",
             "--effort", str(effort)] + _metadata_boxes_flag(),
            CODEC_TIMEOUT
        )
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {_stderr_tail(r.stderr)}")

        # WHICH source made this output. Written BEFORE reorder_jxl_boxes:
        # every exiftool edit re-appends its boxes after the codestream, so
        # doing it afterwards would undo the reorder (bug #134's class).
        # NEVER written into a JXL that carries a jbrd box, though: appending
        # XMP to a JPEG-reconstruction container makes djxl --reconstruct_jpeg
        # FAIL when the source JPEG already had XMP metadata — the original
        # JPEG silently stops being bit-exactly recoverable while the delete
        # gate still certifies the output (v2.0.0+ data-loss bug). Provenance
        # for this path is the checksums.md5 db instead (see
        # _provenance_filter and the delete gate's reconstruction test).
        try:
            if not has_jbrd_box(write_path):
                _run_exiftool_argfile(
                    ["-overwrite_original"] + _provenance_marker_args(src_path)
                    + _history_args("converted", src_path, write_path)
                    + [str(write_path)], timeout=EXIFTOOL_TIMEOUT)
        except Exception as _e_prov:
            logger.debug(f"Provenance marker skipped: {_e_prov}")

        reorder_jxl_boxes(write_path)

        # Validate EVERY successful output (a tool returning 0 does not
        # guarantee a well-formed file); only then record the checksum —
        # the MD5 db must never claim coverage of an invalid output.
        if not _verify_file_integrity(write_path):
            raise RuntimeError("tool returned 0 but the output failed the integrity check")

        if src_md5:
            # Also record the JXL's OWN md5 under the suffixed key. The plain
            # entry binds the db to the SOURCE's bytes; this one binds it to
            # the OUTPUT's — the decode-side gates can then prove the JXL on
            # disk is the very file this run wrote, not a swapped same-named
            # replacement. Hashed AFTER reorder_jxl_boxes + the integrity
            # check, so it is the exact bytes that reached the destination.
            jxl_md5 = md5_of_file(write_path)
            if not _promote_local:
                # Staging: the staging db, under the final name (no uuid).
                # The mover files the lines at the destination.
                checksum_path = write_path.parent / final_path.name
                store_md5_db(checksum_path, src_md5)
                store_jxl_self_hash_db(checksum_path, jxl_md5)

        if _promote_local:
            # Atomic same-folder swap: the final name only ever names a
            # verified, complete file. A failure raises into the except
            # handler, which deletes the temp.
            _swap_into_place(write_path, final_path)
            # The lines only AFTER the swap (#556). Filed first, they named a
            # file the swap had not replaced yet: a run stopped in between
            # left the OLD archive under the NEW source's checksum. Stopped
            # here instead, the JXL has no line yet, and the next deleting
            # run adopts it by reconstruction.
            if src_md5:
                _file_checksum_lines(final_path, src_md5, jxl_md5)

        n, total = next_count()
        label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {src_path.name} -> {final_path.name}")
        # result[4] carries the JXL self-hash so the staging mover can file it
        # next to the source hash at the destination.
        return (str(src_path), "reconvert" if overwritten else "ok", str(final_path),
                src_md5, jxl_md5)
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
    # Never write under the final name without staging (see
    # encode_one_transcode): uuid temp beside the final + atomic os.replace
    # after the integrity check. The ".tmp" suffix (not the final ".jpg")
    # follows the same rule as there: no orphan adoptable as real input
    # (item 26); djxl needs --output_format for a non-standard extension,
    # added on the command below.
    _promote_local = write_path == final_path
    if _promote_local:
        write_path = final_path.parent / f"{uuid.uuid4().hex}_{final_path.name}.tmp"
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
        # The beside-final temp ends in ".tmp" (item 26), and djxl infers the
        # output format from the extension — say which one it is. A no-op
        # when staging supplies a real extension.
        if _tool_at_least("djxl", 0, 12):
            if is_jxl_decode:
                djxl_cmd.insert(1, "--output_format=jpeg")
            elif write_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                djxl_cmd.insert(1, "--output_format=png")
        output_dirty = True
        r = _run_captured(djxl_cmd, CODEC_TIMEOUT)
        repaired_copy = None
        if (r.returncode != 0 and is_jxl_decode and AUTO_REPAIR_JBRD
                and _tool_at_least("djxl", 0, 12)):
            # v2.0.0-v2.0.3 wrote XMP markers into jbrd containers, which makes
            # --reconstruct_jpeg fail. Repair a COPY (the JXL is never
            # modified) and decode from it. Healing the archive itself stays a
            # separate, deliberate step: --repair-jbrd.
            repaired_copy = _auto_repair_copy(jxl_path)
            if repaired_copy is not None:
                logger.warning(f" AUTO-REPAIR (markers stripped on a copy; the JXL "
                               f"itself is unchanged — heal the archive with "
                               f"--repair-jbrd) | {jxl_path.name}")
                try:
                    r = _run_captured(["djxl", "--reconstruct_jpeg",
                                       "--output_format=jpeg",
                                       str(repaired_copy), str(write_path)],
                                      CODEC_TIMEOUT)
                finally:
                    shutil.rmtree(repaired_copy.parent, ignore_errors=True)
        if r.returncode != 0:
            hint = ""
            if is_jxl_decode and _tool_at_least("djxl", 0, 12):
                hint = (" | jbrd reconstruction failed — if this JXL was written by "
                        "v2.0.0-v2.0.3, its toolkit XMP markers are the likely cause. "
                        "Safest: copy the file and run --repair-jbrd on the copy (it "
                        "only replaces a file when the repair provably reconstructs). "
                        "Or re-run with --auto-repair-jbrd to decode from a repaired "
                        "copy without touching the JXL at all")
            raise RuntimeError(f"djxl: {_stderr_tail(r.stderr)}{hint}")

        # Validate the decoded output itself (rc=0 does not guarantee a
        # well-formed file). The MD5 comparison below is an even stronger
        # check, but only runs when a checksum was stored. When the
        # structural check fails, a REAL md5 match against the stored
        # original's hash may still accept the file: a JPEG with a large
        # appended trailer (Motion Photo) is complete and bit-exact even
        # where a structural heuristic refuses it, and the decode path was
        # rejecting exactly such files AFTER the encoder had already deleted
        # the source (2026-09-21 audit, item 2). Fail-closed: only an actual
        # md5_of_file comparison shortcuts the structural check — never a
        # missing/failed one.
        _structural_ok = _verify_file_integrity(write_path)
        _recovered_md5 = None
        if not _structural_ok and verify and stored_md5 is not None:
            try:
                _recovered_md5 = md5_of_file(write_path)
            except OSError:
                _recovered_md5 = None
            if _recovered_md5 == stored_md5:
                _structural_ok = True
        if not _structural_ok:
            raise RuntimeError("djxl returned 0 but the output failed the integrity check")

        n, total = next_count()

        # result[3] carries "md5 verified this run" for the delete gate:
        # the source may only be deleted without djxl>=0.12's authoritative
        # --reconstruct_jpeg when the MD5 comparison actually ran AND passed.
        md5_verified = None
        if verify:
            if repaired_copy is not None:
                # The repaired copy reconstructs a JPEG with identical image
                # data but re-serialized XMP — its md5 is not the original
                # JPEG's BY DESIGN, so comparing would fail a good decode.
                logger.info(f"[{n}/{total}] OK [auto-repaired copy; image data identical, "
                            f"metadata bytes differ from the original JPEG] | {jxl_path.name}")
            elif stored_md5 is None:
                logger.warning(f"[{n}/{total}] OK (no MD5 stored) | {jxl_path.name}")
            else:
                # Reuse the hash the structural-check fallback may already
                # have computed (item 2) instead of reading the file again.
                recovered_md5 = (_recovered_md5 if _recovered_md5 is not None
                                 else md5_of_file(write_path))
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
            logger.info(f"[{n}/{total}] OK | {jxl_path.name} -> {final_path.name}")

        if _promote_local:
            # Atomic same-folder swap: the final name only ever names a
            # verified, complete file (MD5-verified when a hash was stored).
            _swap_into_place(write_path, final_path)

        status = "reconvert" if overwritten else "ok"
        if repaired_copy is not None:
            _auto_repaired.add(str(jxl_path))
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

_capture_output_identity = _common._capture_output_identity


_delete_partial_if_written = _common._delete_partial_if_written


def _jxl_reconstruct_md5(jxl_path: Path) -> Optional[str]:
    """MD5 of the JPEG `djxl --reconstruct_jpeg` rebuilds from this JXL, or
    None when the reconstruction fails, is empty, or cannot run (djxl < 0.12).

    Does NOT compare against any original: it answers "is the original JPEG
    recoverable at all, and what are its bytes", which is what the
    --repair-jbrd audit needs when the original may already be gone.
    """
    if not _tool_at_least("djxl", 0, 12):
        return None
    fd, tmp_name = tempfile.mkstemp(suffix=".jpg", prefix="jxlrec_", dir=TEMP_DIR)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        r = _run_captured(
            ["djxl", "--reconstruct_jpeg", str(jxl_path), str(tmp)],
            CODEC_TIMEOUT)
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


def _jxl_reconstruct_ok(jxl_path: Path) -> bool:
    """True iff `djxl --reconstruct_jpeg` succeeds and yields a non-empty JPEG."""
    return _jxl_reconstruct_md5(jxl_path) is not None


def _jxl_reconstructs_to(jxl_path: Path, jpeg_path: Path) -> bool:
    """True iff `djxl --reconstruct_jpeg` on the JXL reproduces EXACTLY the
    bytes of `jpeg_path`.

    This is the REAL recovery gate for jbrd-carrying JXLs. Structural
    integrity + the presence of a jbrd box are not enough: v2.0.0-v2.0.3 wrote
    XMP provenance markers into jbrd containers, which makes
    --reconstruct_jpeg fail for any source JPEG that already had XMP — and
    the old delete gate (jbrd present + integrity OK + MD5 recorded) certified
    exactly such files, so --delete-source destroyed originals that could no
    longer be recovered bit-exactly.

    Unlike _jxl_binds_to_archived_jpeg there is NO self-hash shortcut: a
    self-hash only proves the JXL is the file this run wrote, not that it
    still reconstructs. Every check that cannot run (djxl < 0.12, tool error,
    empty reconstruction) returns False: this guards an irreversible delete,
    so it fails CLOSED.
    """
    if not _tool_at_least("djxl", 0, 12):
        return False
    fd, tmp_name = tempfile.mkstemp(suffix=".jpg", prefix="jxlrec_", dir=TEMP_DIR)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        r = _run_captured(
            ["djxl", "--reconstruct_jpeg", str(jxl_path), str(tmp)],
            CODEC_TIMEOUT)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            return False
        return md5_of_file(tmp) == md5_of_file(jpeg_path)
    except Exception:
        # A proof that could not run is not a proof.
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _adopt_reconstructed_archive(src_path: Path, jxl_path: Path,
                                 dry_run: bool = False) -> bool:
    """Adopt a jbrd JXL that has NO checksums.md5 line but that
    `djxl --reconstruct_jpeg` turns back into exactly src_path's bytes (#556).

    Such a file is that JPEG's archive, written by a run that stopped after the
    JXL reached its folder and before its checksum lines did (killed, Ctrl+C,
    a power cut), or with --no-md5. The reconstruction proves more than the
    line itself would, so the lines (source md5, JXL self-hash) are written now
    and the caller treats the pair as archived. False, and nothing written,
    when it does not reconstruct, cannot be checked (djxl < 0.12 — and a dry
    run, which runs no subprocess), STORE_MD5 is off, or the line could not be
    recorded (the db lock): the caller then fails closed as before.
    """
    if dry_run or not STORE_MD5 or not has_jbrd_box(jxl_path):
        return False
    # Hashed before AND after the reconstruction: a file rewritten while it
    # was being checked must not get a line for bytes nobody compared.
    try:
        src_md5, jxl_md5 = md5_of_file(src_path), md5_of_file(jxl_path)
    except OSError:
        return False
    if not _jxl_reconstructs_to(jxl_path, src_path):
        return False
    try:
        if md5_of_file(src_path) != src_md5 or md5_of_file(jxl_path) != jxl_md5:
            return False
        store_md5_db(jxl_path, src_md5)
        store_jxl_self_hash_db(jxl_path, jxl_md5)
    except OSError as e:
        logger.warning(f" could not record the checksum of {jxl_path.name} ({e})")
        return False
    if read_md5_db(jxl_path) is None:
        return False
    logger.info(f" ADOPTED {jxl_path.name}: an earlier run wrote it but stopped before "
                f"recording its checksum; djxl --reconstruct_jpeg gives {src_path.name} "
                f"back from it byte for byte")
    return True


def _jxl_binds_to_archived_jpeg(jxl_path: Path, archived_jpeg: Path,
                                stored_jpeg_md5: Optional[str]) -> bool:
    """Prove the JXL on disk is the file the archived JPEG came from.

    The plain provenance check (stored JPEG md5 == md5 of the JPEG on disk)
    is NAME-keyed: the stored hash was filed under the JXL's name, so if
    photo.jxl is REPLACED by a different same-named JXL, the old JPEG still
    matches the stored hash and the replacement sails through both decode
    gates — and --delete-source then destroys a JXL that was never archived.

    This check binds the gate to the JXL's CONTENT instead, two ways:

      1. Self-hash (databases written by this version or later): the encoder
         stores the JXL's own md5 under "<name>.jxl-md5". Comparing it against
         the current file is an exact identity proof, no decode needed.
      2. Legacy databases (no self-hash line): fall back to
         djxl --reconstruct_jpeg into a temp file and compare the
         reconstruction against the archived JPEG (and the stored hash when
         one exists). A match proves THIS JXL really reconstructs to the
         archived JPEG.

    Either check failing — or being UNAVAILABLE (djxl<0.12 has no
    --reconstruct_jpeg, the tool errored, the reconstruction is empty) —
    returns False: this guards an irreversible delete, so it fails CLOSED.
    """
    self_hash = read_jxl_self_hash_db(jxl_path)
    if self_hash is not None:
        # The hash read itself (locked file, vanished mid-gate) must not
        # escape this function: an exception here would kill the whole run
        # instead of failing THIS proof closed (item 23 — md5_of_file was
        # outside the try that shields the rest of the checks).
        try:
            return md5_of_file(jxl_path) == self_hash
        except OSError:
            return False
    if not _tool_at_least("djxl", 0, 12):
        logger.debug(f" content-binding unavailable (legacy db, djxl<0.12) | {jxl_path.name}")
        return False
    fd, tmp_name = tempfile.mkstemp(suffix=".jpg", prefix="jxlbind_", dir=TEMP_DIR)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        r = _run_captured(
            ["djxl", "--reconstruct_jpeg", str(jxl_path), str(tmp)],
            CODEC_TIMEOUT)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            return False
        rec_md5 = md5_of_file(tmp)
        if stored_jpeg_md5 is not None and rec_md5 != stored_jpeg_md5:
            return False
        return rec_md5 == md5_of_file(archived_jpeg)
    except Exception:
        # A proof that could not run is not a proof.
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _strip_provenance_markers(jxl_path: Path, info: dict):
    """Remove the jxlphoto-src/srcsum tokens this toolkit wrote into
    XMP-dc:Relation of `jxl_path`. Returns None on success, else the reason.

    These markers were appended with `+=`, so any values the source JPEG
    already had in dc:Relation survive; only our two tokens are removed.
    EVERY marker value is removed, not just the last one: a file written
    through more than one pass can hold several pairs, and stripping only one
    left the reconstruction broken and the file reported STILL BROKEN though
    it was repairable. exiftool refuses some files over a "[minor]" metadata
    oddity (seen on JPEGs whose Exif references a Brotli directory): the edit
    is retried with -m, and the exit code is CHECKED -- an edit that did not
    happen must never be reported as "markers stripped", and the post-check
    asserts NO jxlphoto-src/srcsum token remains. reorder_jxl_boxes runs
    afterwards because every exiftool edit re-appends its boxes after the
    codestream (bug #134's class).
    """
    srcs, srcsums = [], []
    if info.get("src") or info.get("srcsum"):
        # `info` proves the file carries markers (read by _read_source_markers_batch,
        # which keeps only the LAST of each); read EVERY value so a file with
        # more than one pair is fully stripped.
        srcs, srcsums = _read_all_source_marker_values(jxl_path)
        if not (srcs or srcsums):
            # A read failure (empty) falls back to the values the caller already
            # read, so a strip attempt still happens; the reconstruction test in
            # _strip_markers_proving_repair is the proof either way.
            if info.get("src"):
                srcs = [info["src"]]
            if info.get("srcsum"):
                srcsums = [info["srcsum"]]
    if not (srcs or srcsums):
        return "no toolkit markers to remove"
    lines = ["-overwrite_original"]
    # One -= per DISTINCT value: -TAG-=VAL already removes every copy of the
    # same value, but a second pair with a DIFFERENT id needs its own removal.
    for value in dict.fromkeys(srcs):
        lines.append("-XMP-dc:Relation-=" + SRC_PREFIX + value)
    for value in dict.fromkeys(srcsums):
        lines.append("-XMP-dc:Relation-=" + SRCSUM_PREFIX + value)
    last_err = ""
    for extra in ([], ["-m"]):
        try:
            r = _run_exiftool_argfile(extra + lines + [str(jxl_path)], timeout=EXIFTOOL_TIMEOUT)
        except Exception as e:
            last_err = str(e)
            continue
        if r.returncode == 0:
            break
        last_err = (r.stderr or r.stdout or f"rc={r.returncode}").strip()[:200]
    else:
        return f"exiftool could not edit the file ({last_err})"
    after_srcs, after_srcsums = _read_all_source_marker_values(jxl_path)
    if after_srcs or after_srcsums:
        return "exiftool reported success but the markers are still there"
    reorder_jxl_boxes(jxl_path)
    return None


def _strip_markers_proving_repair(jxl_path: Path, tmp: Path):
    """(rec_md5, detail) — copy jxl_path to tmp, strip the toolkit's provenance
    markers ON THE COPY and retest reconstruction. (None, why) when the copy
    still does not reconstruct. jxl_path itself is never touched; the caller
    owns tmp's cleanup."""
    info = (_read_source_markers_batch([jxl_path]).get(str(jxl_path))
            or {"src": None, "srcsum": None})
    if not (info.get("src") or info.get("srcsum")):
        return None, "reconstruction fails and there are no toolkit markers to remove"
    shutil.copy2(str(jxl_path), str(tmp))
    why = _strip_provenance_markers(tmp, info)
    if why is not None:
        return None, f"{why}; the file was left untouched"
    rec_md5 = _jxl_reconstruct_md5(tmp)
    if rec_md5 is None:
        return None, ("markers removed on a copy, but the reconstruction "
                      "still fails; the file was left untouched")
    return rec_md5, "markers removable; reconstruction then succeeds"


def _auto_repair_copy(jxl_path: Path):
    """A repaired COPY of a jbrd JXL whose reconstruction fails, or None.

    Used by AUTO_REPAIR_JBRD on the decode path: the repaired copy lives in
    the temp (TEMP_DIR when set, else the system temp — never beside the
    source, where a *.jxl glob would pick it up next run), the original JXL
    is never modified — healing the archive is --repair-jbrd's job. The temp
    wears a ".tmp" name (same rule as _repair_one_jbrd): if TEMP_DIR points
    INTO the archive, a hard kill must not leave an adoptable *.jxl there.
    Caller deletes the returned path's parent dir.
    """
    _dir = str(TEMP_DIR) if TEMP_DIR else None
    tmp_dir = Path(tempfile.mkdtemp(prefix="jxl_autorepair_", dir=_dir))
    tmp = tmp_dir / f"{jxl_path.stem}.tmp"
    try:
        rec_md5, _detail = _strip_markers_proving_repair(jxl_path, tmp)
    except Exception:
        rec_md5 = None
    if rec_md5 is None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None
    return tmp


def _repair_one_jbrd(jxl_path: Path, dry_run: bool):
    """(state, detail) for one JXL whose reconstruction fails.

    The repair runs on a COPY next to the original and replaces it only when
    the copy provably reconstructs: a failed attempt leaves the original
    byte-for-byte untouched (editing the archive in place kept the edit even
    when the reconstruction still failed). A dry run does the same work on
    the copy and throws it away, so "WOULD REPAIR" is a tested prediction,
    not a guess from the presence of the markers.

    After a real repair the checksums.md5 lines for this name are refreshed:
    the reconstructed JPEG's md5 (the stored one is the ORIGINAL JPEG's, which
    differs in its re-serialized XMP bytes -- the image data is identical) and
    the JXL's new self-hash. Without that every later lossless decode failed
    its MD5 verification and the delete gates refused the file forever.
    """
    # .tmp, not .jxl: a crash between here and the finally would otherwise
    # leave a *.jxl next to the source that the next recursive scan treats as
    # input. djxl/exiftool read content, not extensions. On a DRY RUN the
    # copy goes to TEMP_DIR instead (like _jxl_reconstruct_md5's decode
    # temp): the audit promised "nothing is written", and beside-the-source
    # writes fail outright on read-only media — the audit marked every file
    # there STILL BROKEN (2026-09-21 audit, item 27). Same `.tmp` suffix.
    if dry_run:
        _dry_dir = Path(TEMP_DIR) if TEMP_DIR else Path(tempfile.gettempdir())
        _dry_dir.mkdir(parents=True, exist_ok=True)
        tmp = _dry_dir / f"{uuid.uuid4().hex}_repair_{jxl_path.stem}.tmp"
    else:
        tmp = jxl_path.parent / f"{uuid.uuid4().hex}_repair_{jxl_path.stem}.tmp"
    try:
        st = jxl_path.stat()
        rec_md5, detail = _strip_markers_proving_repair(jxl_path, tmp)
        if rec_md5 is None:
            return "broken", detail
        if dry_run:
            return "repairable", detail
        if _changed_since(jxl_path, st):
            return "broken", ("the file changed while it was being repaired (another "
                              "run?) — left untouched; run --repair-jbrd again")
        # The archive keeps its date, like the relayout (#580): exiftool
        # stamped the copy with "now", and a sync run compares this mtime
        # with its source's.
        os.utime(str(tmp), ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(str(tmp), str(jxl_path))
        if (read_md5_db(jxl_path) is not None
                or read_jxl_self_hash_db(jxl_path) is not None):
            store_md5_db(jxl_path, rec_md5)
            store_jxl_self_hash_db(jxl_path, md5_of_file(jxl_path))
            return "repaired", "checksums.md5 updated for the repaired file"
        return "repaired", "no checksums.md5 entry to update"
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


_JXL_SIGNATURE = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
_METADATA_TYPES = (b"Exif", b"xml ")


def _parse_jxl_boxes(data: bytes):
    """[(type, header, payload)] of a JXL container held in memory, or None
    when it is not a container or a box is truncated or malformed."""
    if data[:12] != _JXL_SIGNATURE:
        return None
    boxes, i, n = [], 0, len(data)
    while i < n:
        if i + 8 > n:
            return None
        size, typ, hdr = int.from_bytes(data[i:i + 4], "big"), data[i + 4:i + 8], 8
        if size == 1:
            if i + 16 > n:
                return None
            size, hdr = int.from_bytes(data[i + 8:i + 16], "big"), 16
        elif size == 0:
            size = n - i
        if size < hdr or i + size > n:
            return None
        boxes.append((typ, data[i:i + hdr], data[i + hdr:i + size]))
        i += size
    return boxes


def _irfanview_layout_problem(jxl_path: Path) -> Optional[str]:
    """Why IrfanView would show no EXIF for this JXL, or None.

    IrfanView reads the EXIF only from a PLAIN Exif box placed before the
    image data and before the XMP (#561, #562). Reads box headers only (plus
    the 4-byte inner type of a brob box); a file it cannot parse is None —
    the repair then leaves it alone."""
    try:
        with open(jxl_path, "rb") as f:
            if f.read(12) != _JXL_SIGNATURE:
                return None
            end = f.seek(0, 2)
            pos, kinds = 12, []
            while pos + 8 <= end:
                f.seek(pos)
                head = f.read(8)
                size, typ, hdr = int.from_bytes(head[:4], "big"), head[4:8], 8
                if size == 1:
                    size, hdr = int.from_bytes(f.read(8), "big"), 16
                elif size == 0:
                    size = end - pos
                if size < hdr or pos + size > end:
                    return None
                if typ == b"brob":
                    inner = f.read(4)
                    if inner in _METADATA_TYPES:
                        kinds.append(("brob", inner))
                elif typ in _METADATA_TYPES:
                    kinds.append(("plain", typ))
                elif typ in (b"jxlc", b"jxlp"):
                    kinds.append(("image", typ))
                pos += size
    except (OSError, ValueError):
        return None
    if any(k == "brob" for k, _t in kinds):
        return "compressed EXIF/XMP"
    order = [t for _k, t in kinds]
    if b"Exif" not in order:
        return None
    first_exif = order.index(b"Exif")
    if any(t in (b"jxlc", b"jxlp") for t in order[:first_exif]):
        return "EXIF after the image data"
    if b"xml " in order[:first_exif]:
        return "EXIF after the XMP"
    return None


def _codestream_and_jbrd(boxes):
    """(codestream bytes, jbrd payload) of parsed boxes: what the repair of
    the metadata layout must leave byte-identical."""
    cs = b"".join(p for t, _h, p in boxes if t in (b"jxlc", b"jxlp"))
    jb = b"".join(p for t, _h, p in boxes if t == b"jbrd")
    return cs, jb


def _plain_metadata_copy(jxl_path: Path, tmp: Path, work: Path):
    """(rec_md5, detail): write to `tmp` a copy of the jbrd JXL `jxl_path`
    whose Exif/XMP boxes are plain and placed where IrfanView reads them.
    (None, why) when that cannot be done and proven; jxl_path is never
    touched, the caller owns tmp.

    The plain boxes come from cjxl itself: the JPEG is rebuilt
    (djxl --reconstruct_jpeg) and encoded again with --compress_boxes=0; its
    Exif/xml boxes replace the brob ones, one for one, and everything else
    keeps its bytes. The codestream and the jbrd box must come out
    byte-identical, and the copy must rebuild the very same JPEG (MD5)."""
    data = jxl_path.read_bytes()
    boxes = _parse_jxl_boxes(data)
    if boxes is None:
        return None, "unreadable container"
    rec_jpg, fresh_jxl = work / "rebuilt.jpg", work / "plain.jxl"
    r = _run_captured(["djxl", "--reconstruct_jpeg", str(jxl_path), str(rec_jpg)],
                      CODEC_TIMEOUT)
    if r.returncode != 0 or not rec_jpg.exists() or rec_jpg.stat().st_size == 0:
        return None, "the JPEG could not be rebuilt"
    rec_md5 = md5_of_file(rec_jpg)
    r = _run_captured(["cjxl", str(rec_jpg), str(fresh_jxl), "--lossless_jpeg=1",
                       "--effort", "1"] + _PLAIN_BOXES_FLAG, CODEC_TIMEOUT)
    fresh = _parse_jxl_boxes(fresh_jxl.read_bytes()) if r.returncode == 0 and fresh_jxl.exists() else None
    if fresh is None:
        return None, f"cjxl could not re-read the JPEG ({_stderr_tail(r.stderr)})"
    plain = {k: [b for b in fresh if b[0] == k] for k in _METADATA_TYPES}
    used = {k: 0 for k in _METADATA_TYPES}
    out = []
    for typ, hdr, payload in boxes:
        kind = payload[:4] if typ == b"brob" else None
        if kind in _METADATA_TYPES:
            if used[kind] >= len(plain[kind]):
                return None, "cjxl wrote fewer metadata boxes than the file holds"
            box = plain[kind][used[kind]]
            used[kind] += 1
            out.append(box[1] + box[2])
        else:
            out.append(hdr + payload)
    tmp.write_bytes(b"".join(out))
    reorder_jxl_boxes(tmp)      # Exif first, all metadata before the codestream
    new_boxes = _parse_jxl_boxes(tmp.read_bytes())
    if new_boxes is None or _codestream_and_jbrd(new_boxes) != _codestream_and_jbrd(boxes):
        return None, "the image data or the jbrd box would change"
    if not _verify_file_integrity(tmp):
        return None, "the rewritten copy failed the integrity check"
    if _jxl_reconstruct_md5(tmp) != rec_md5:
        return None, "the rewritten copy does not rebuild the same JPEG"
    return rec_md5, "EXIF readable by IrfanView"


def _relayout_one_jbrd(jxl_path: Path, dry_run: bool, problem: str):
    """(state, detail) — fix the metadata layout of a jbrd JXL that
    reconstructs fine but shows IrfanView no EXIF (#562). state: "relaid" /
    "would_relay" / "failed". Same rules as _repair_one_jbrd: the work is
    done on a copy (beside the file, in TEMP_DIR on a dry run), proven, and
    swapped in with os.replace only if the file did not change meanwhile.
    The file keeps its modified date; when checksums.md5 has lines for it,
    the JXL's new self-hash is appended (the JPEG's line stays valid: the
    rebuilt JPEG is the same)."""
    _dir = Path(TEMP_DIR) if TEMP_DIR else Path(tempfile.gettempdir())
    if dry_run:
        _dir.mkdir(parents=True, exist_ok=True)
        tmp = _dir / f"{uuid.uuid4().hex}_repair_{jxl_path.stem}.tmp"
    else:
        tmp = jxl_path.parent / f"{uuid.uuid4().hex}_repair_{jxl_path.stem}.tmp"
    work = Path(tempfile.mkdtemp(prefix="jxl_relayout_", dir=str(TEMP_DIR) if TEMP_DIR else None))
    try:
        st = jxl_path.stat()
        rec_md5, detail = _plain_metadata_copy(jxl_path, tmp, work)
        if rec_md5 is None:
            return "failed", f"{problem}: {detail}; the file was left untouched"
        if dry_run:
            return "would_relay", problem
        os.utime(str(tmp), ns=(st.st_atime_ns, st.st_mtime_ns))
        if _changed_since(jxl_path, st):
            return "failed", (f"{problem}: the file changed meanwhile (another run?) "
                              "— left untouched")
        os.replace(str(tmp), str(jxl_path))
        if (read_md5_db(jxl_path) is not None
                or read_jxl_self_hash_db(jxl_path) is not None):
            store_jxl_self_hash_db(jxl_path, md5_of_file(jxl_path))
            return "relaid", f"{problem}; checksums.md5 updated"
        return "relaid", problem
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def cmd_repair_jbrd(args) -> int:
    """Audit (and fix) JXLs whose jbrd reconstruction is broken.

    v2.0.0-v2.0.3 wrote XMP provenance markers into jbrd containers; for any
    source JPEG that already had XMP that makes djxl --reconstruct_jpeg fail,
    so the original JPEG is no longer recoverable bit-exactly even though the
    image data is intact. For every JXL under the input root:

      * no jbrd box          -> not applicable (nothing to reconstruct)
      * reconstruction OK    -> fine, left untouched
      * reconstruction fails -> the markers are stripped ON A COPY and the
                                reconstruction retested; only a copy that
                                reconstructs replaces the file (REPAIRED),
                                otherwise the file is left as it was
                                (STILL BROKEN)

    A repaired file rebuilds a JPEG with IDENTICAL image data; its metadata
    bytes differ from the original (exiftool re-serializes the XMP packet),
    so its MD5 is not the original's. With --dry-run nothing is written.

    Every jbrd JXL that reconstructs (or was just repaired) is also checked
    for the EXIF layout IrfanView needs (#562): a Brotli-compressed Exif/XMP
    box (the cjxl default before v2.19.2), or an Exif box after the XMP or
    the image data, is rewritten by _relayout_one_jbrd — codestream and jbrd
    untouched, the very same JPEG rebuilt, or the file is left as it was.
    Exit code: 0 when nothing is STILL BROKEN, 1 otherwise (a layout that
    could not be fixed is a warning: the archive itself is fine).
    """
    log_file = setup_logger()
    root = args.input
    files = [root] if root.is_file() else sorted(root.rglob("*.jxl"))
    files = [f for f in files if f.is_file()]
    logger.info(f"Repair: auditing {len(files)} JXL file(s) under {root}"
                + (" (dry run: nothing is written)" if args.dry_run else ""))
    if not args.dry_run:
        _sweep_orphan_temps(files)
    counts = {"ok": 0, "repaired": 0, "broken": 0, "no_jbrd": 0,
              "relaid": 0, "relay_failed": 0}
    lock = threading.Lock()
    t0 = time.time()
    # The EXIF layout step (#562) needs cjxl; the reconstruction repair does
    # not, and must still run on a machine without it.
    relayout = not COMPRESS_METADATA_BOXES and shutil.which("cjxl") is not None
    if not COMPRESS_METADATA_BOXES and not relayout:
        logger.warning("cjxl not found: the EXIF layout for IrfanView is not checked "
                       "in this run (the JPEG recovery is)")

    def work(f):
        lines = []          # (level, text), logged together under the lock
        if not has_jbrd_box(f):
            return "no_jbrd", False, lines
        state = "ok"
        if not _jxl_reconstruct_ok(f):
            try:
                state, detail = _repair_one_jbrd(f, args.dry_run)
            except Exception as e:
                # A locked/read-only/in-use file must not abort the whole audit:
                # report it as still broken and keep walking the remaining files.
                state, detail = "broken", f"repair raised {type(e).__name__}: {e}"
            if state == "repaired":
                lines.append((logger.warning,
                              f"REPAIRED (JPEG recoverable again with identical image "
                              f"data; its metadata bytes differ from the original -- "
                              f"{detail})"))
            elif state == "repairable":
                state = "repaired"
                lines.append((logger.warning, f"WOULD REPAIR ({detail})"))
            else:
                lines.append((logger.error, f"STILL BROKEN ({detail})"))
                return "broken", False, lines
        # IrfanView: plain Exif before the XMP and the image data (#562). A
        # file this run just repaired is checked on disk; in a dry run the
        # unrepaired original is what gets looked at.
        # COMPRESS_METADATA_BOXES: the user chose size over IrfanView.
        relaid = False
        problem = _irfanview_layout_problem(f) if relayout else None
        if problem and not (args.dry_run and state == "repaired"):
            try:
                rstate, rdetail = _relayout_one_jbrd(f, args.dry_run, problem)
            except Exception as e:
                rstate, rdetail = "failed", f"{problem}: {type(e).__name__}: {e}"
            if rstate == "failed":
                lines.append((logger.warning, f"EXIF LAYOUT LEFT AS IS ({rdetail})"))
                relaid = None
            else:
                relaid = True
                lines.append((logger.info,
                              ("WOULD MAKE EXIF READABLE" if args.dry_run
                               else "EXIF MADE READABLE") + f" ({rdetail})"))
        if state == "ok" and not lines:
            lines.append((logger.info, "OK"))
        return state, relaid, lines

    def run_one(f):
        try:
            state, relaid, lines = work(f)
        except Exception as e:
            state, relaid, lines = "broken", False, [
                (logger.error, f"STILL BROKEN (audit raised {type(e).__name__}: {e})")]
        with lock:
            counts[state] += 1
            if relaid:
                counts["relaid"] += 1
            elif relaid is None:
                counts["relay_failed"] += 1
            done = sum(counts[k] for k in ("ok", "repaired", "broken", "no_jbrd"))
            left = (time.time() - t0) / done * (len(files) - done)
            eta = f"  ~{left / 60:.0f} min left" if done % 25 == 0 and left > 60 else ""
            for log, text in lines:
                log(f" [{done}/{len(files)}] {text} | {f.name}{eta}")
            if not lines and eta:
                logger.info(f" [{done}/{len(files)}]{eta}")

    with ThreadPoolExecutor(max_workers=max(1, min(getattr(args, "workers", 1) or 1, 8))) as pool:
        list(pool.map(run_one, files))
    ok, repaired = counts["ok"], counts["repaired"]
    still_broken, no_jbrd = counts["broken"], counts["no_jbrd"]
    logger.info(f"Repair summary: {ok} OK, {repaired} "
                + ("repairable (dry run)" if args.dry_run else "repaired")
                + f", {still_broken} still broken, {no_jbrd} without jbrd")
    if counts["relaid"] or counts["relay_failed"]:
        logger.info(f"EXIF for IrfanView: {counts['relaid']} file(s) "
                    + ("would be fixed (dry run)" if args.dry_run else "fixed")
                    + (f", {counts['relay_failed']} left as they were (see the warnings)"
                       if counts["relay_failed"] else ""))
    if still_broken:
        logger.error("These files can no longer reproduce their original "
                     "JPEG -- keep any source JPEGs you still have.")
    # Emit the machine-readable summary from INSIDE the repair path: main()
    # routes the repair with sys.exit(cmd_repair_jbrd(...)) before its own
    # emit_summary_json, so --summary-json used to print nothing at all for a
    # repair run (and the wrapper labelled it "[no summary]").
    _extras = {"without jbrd (not applicable)": no_jbrd}
    if args.dry_run:
        _extras["repairable (dry run)"] = repaired
    if counts["relaid"]:
        _extras["EXIF made readable by IrfanView"
                + (" (dry run)" if args.dry_run else "")] = counts["relaid"]
    if counts["relay_failed"]:
        _extras["EXIF layout left as is"] = counts["relay_failed"]
    record_summary(ok=ok + repaired, overwritten=0, skipped=no_jbrd,
                   errors=still_broken, log_file=log_file, extras=_extras,
                   dry_run=bool(args.dry_run))
    emit_summary_json(args.summary_json)
    return 1 if still_broken else 0


@_holds_staging
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

    _auto_repaired.clear()

    ext = ".jpg" if decode else ".jxl"
    tasks = []
    for src, final_out in group_pairs:
        write_out = (staging_dir / f"{uuid.uuid4().hex}_{src.stem}{ext}") if use_staging else final_out
        tasks.append((src, write_out, final_out))

    moved_finals = set()
    refused_moves = {}      # str(src) -> why (#615)
    failed_moves = set()    # str(src) (#624)

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
            _why = _output_changed_since_planned(final_out)
            if _why:
                refused_moves[str(src)] = _why
                logger.error(f"  REFUSED, kept in staging | {write_out.name} -> "
                             f"{final_out} | {_why}")
                continue
            if _promote_from_staging(write_out, final_out):
                moved += 1
                moved_finals.add(os.path.normcase(str(final_out)))
                if not decode:
                    _file_checksums(final_out, result_by_src.get(str(src)))
            else:
                failed_moves.add(str(src))
        if moved:
            logger.info(f" -> Moved {moved} file(s) from staging to {dest_tasks[0][2].parent}")

    # The destination checksum lines of an output, filed right after its move
    # (#556). They used to be written once the WHOLE run was over: a run
    # stopped in between (killed, Ctrl+C, a power cut) left every folder
    # already moved without them, and later deleting runs refused those JXLs.
    filed = set()
    result_by_src = {}

    def _file_checksums(final_out, r):
        if r is None or r[1] not in ("ok", "reconvert") or len(r) < 4 or not r[3]:
            return
        if _file_checksum_lines(final_out, r[3], r[4] if len(r) > 4 else None):
            filed.add(os.path.normcase(str(final_out)))

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
    for _s, _w, _f in tasks:
        _record_source_identity(_s)      # X2: before anything reads it
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
            result_by_src[result[0]] = result

            # Flush this destination folder once every one of its files is done.
            # Runs on the main thread (inside the as_completed loop), so no two
            # moves ever race for the same file.
            dest = task[2].parent
            pending_by_dest[dest] -= 1
            if use_staging and pending_by_dest[dest] == 0:
                _move_dest_from_staging(tasks_by_dest[dest], status_map)

    if use_staging:
        if not decode:  # Only for encode (decode doesn't create checksums in staging)
            # Distribute each checksum to the folder its own output landed in,
            # keyed by the TASK.
            #
            # This used to parse the staging checksums.md5 back and match its
            # lines by output FILENAME. Two sources with the same basename —
            # a/photo.jpg and b/photo.jpg, both becoming photo.jxl in different
            # destination folders — collapsed to one key: both lines were filed
            # under one folder and the other got none. Worse, the "unmatched"
            # fallback appended lines to the first successful task's folder,
            # writing a hash for a file that is not there. A later decode of
            # that folder verifies against a foreign hash and reports MD5-FAIL
            # on a perfectly good file.
            #
            # The md5 is already in each result (index 3), so nothing needs to
            # be parsed back at all.
            _stranded = 0
            for _r in results:
                if _r[1] not in ("ok", "reconvert") or len(_r) < 4 or not _r[3]:
                    continue
                _final = Path(_r[2])
                # Only for outputs that actually reached their destination: a
                # checksum must never claim coverage of a file still in staging.
                if os.path.normcase(str(_final)) not in moved_finals:
                    _stranded += 1
                    continue
                if os.path.normcase(str(_final)) in filed:
                    continue                    # filed at its move (#556)
                # Its filing at the move failed: one more try. The JXL's own
                # md5 rode out of the worker at result[4] and goes next to the
                # source hash (the staging copy dies with the staging db).
                _file_checksum_lines(_final, _r[3], _r[4] if len(_r) > 4 else None)
            staging_db = staging_dir / CHECKSUMS_FILENAME
            if staging_db.exists():
                if _stranded:
                    # Something did not leave staging (a failed move). Its md5
                    # was deliberately NOT filed at the destination — but
                    # deleting the staging db too threw the only copy of that
                    # hash away, so a file recovered from staging by hand had
                    # lost its provenance for good.
                    logger.warning(
                        f"  Keeping {staging_db}: {_stranded} output(s) are still in "
                        f"staging and their checksums live only there. Move the file "
                        f"and its line to the destination folder before the next run: "
                        f"a new run starts with an empty staging checksums.md5.")
                else:
                    try:
                        staging_db.unlink()
                    except OSError:
                        pass

    if refused_moves:
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
                # Every other refusal in this gate logs KEEP(reason) and counts;
                # a bare continue made an unverifiable output vanish from the
                # report entirely — a silent bypass of the fail-closed rule.
                _delete_stats["kept"] += 1
                logger.warning(
                    f" KEEP (final output missing) | "
                    f"{src_path.name if src_path is not None else result[0]}")
                continue
            if not src_path.exists():
                # #619: nothing left to delete. Said as such — it used to
                # reach the reconstruction check and blame the jbrd box.
                logger.warning(f" {_SOURCE_GONE} | {src_path.name}")
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
                if (stored is None and not decode
                        and _adopt_reconstructed_archive(src_path, final_file)):
                    stored = read_md5_db(final_file)      # #556
                if stored is None:
                    if DELETE_SOURCE_REQUIRE_MD5:
                        _delete_stats["kept"] += 1
                        logger.warning(
                            f" KEEP (already archived, but no checksum to prove it came "
                            f"from this file) | {src_path.name}")
                        continue
                    if decode:
                        # #581: deleting the JXL on the strength of ANY
                        # same-named JPEG needs a real proof without the
                        # checksum — the reconstruction (the encode side has
                        # its own reconstruction gate below).
                        if not (_tool_at_least("djxl", 0, 12)
                                and _jxl_reconstructs_to(src_path, final_file)):
                            _delete_stats["kept"] += 1
                            logger.warning(
                                f" KEEP (already archived, no checksum, and the JXL "
                                f"does not reconstruct this JPEG) | {src_path.name}")
                            continue
                        logger.debug(f" provenance OK (reconstruction) | {src_path.name}")
                    else:
                        logger.warning(
                            f" DELETING an already-archived source with NO provenance check "
                            f"(no checksum stored) | {src_path.name}")
                else:
                    try:
                        actual = md5_of_file(src_path if not decode else final_file)
                    except OSError as e:
                        _delete_stats["kept"] += 1
                        logger.warning(
                            f" KEEP (checksum could not be verified: {e}) | "
                            f"{src_path.name}")
                        continue
                    if actual != stored:
                        _delete_stats["kept"] += 1
                        logger.warning(
                            f" KEEP (already-archived output does NOT match this source: "
                            f"checksum mismatch) | {src_path.name}")
                        continue
                    if decode and not _jxl_binds_to_archived_jpeg(src_path, final_file, stored):
                        # The JPEG on disk matches the stored hash — but that
                        # hash was filed under this JXL's NAME. Only the
                        # content-binding check sees the JXL's own bytes, and
                        # it just failed: photo.jxl was swapped for a different
                        # same-named file that was never archived. Fail closed.
                        _delete_stats["kept"] += 1
                        logger.warning(
                            f" KEEP (archived JPEG matches, but this JXL is NOT the "
                            f"file it was recovered from — swapped or replaced "
                            f"same-named JXL) | {src_path.name}")
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
                if final_file.suffix.lower() == '.jxl':
                    if not has_jbrd_box(final_file):
                        _delete_stats["kept"] += 1
                        logger.warning(f" KEEP (output has no jbrd box; JPEG not recoverable) | {src_path.name}")
                        continue
                    # THE real gate: a jbrd JXL can pass every check above and
                    # still fail djxl --reconstruct_jpeg — e.g. v2.0.0-v2.0.3
                    # wrote XMP markers into jbrd containers, breaking
                    # reconstruction for sources that had XMP. The source may
                    # only be deleted when the reconstruction ACTUALLY
                    # reproduces its bytes. With djxl < 0.12 the test cannot
                    # run, and an unverifiable output must block deletion.
                    if _tool_at_least("djxl", 0, 12):
                        if not _jxl_reconstructs_to(final_file, src_path):
                            _delete_stats["kept"] += 1
                            logger.warning(
                                f" KEEP (djxl --reconstruct_jpeg does not reproduce "
                                f"this JPEG; run --repair-jbrd to fix markers written "
                                f"by v2.0.0+) | {src_path.name}")
                            continue
                    else:
                        _delete_stats["kept"] += 1
                        logger.warning(f" KEEP (djxl<0.12: bit-exact recovery cannot be verified) | {src_path.name}")
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
            if decode and not was_skipped and str(src_path) in _auto_repaired:
                # The JPEG came from a repaired COPY, not from this JXL: the
                # archive is still broken (v2.0.0-v2.0.3 marker damage) and
                # this run never proved THIS file can recover the original
                # JPEG — the recovered JPEG's metadata bytes differ by design.
                # Fail closed.
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (decoded from an auto-repaired copy; heal the "
                               f"archive with --repair-jbrd before --delete-source) | {src_path.name}")
                continue
            if not _verify_file_integrity(final_file):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (output failed integrity check) | {src_path.name}")
                continue
            if not _flush_to_disk(final_file):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (output could not be flushed to disk) | {src_path.name}")
                continue
            if _source_changed_since_read(src_path):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (source changed since this run read it (re-exported or rewritten during the run?) — its current version was never archived) | {src_path.name}")
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


def _lineless_archive_refusal(jxl_path: Path, jpeg_path: Path, decode: bool) -> Optional[str]:
    """Why an overwrite across a lossless JPEG archive that has NO checksums.md5
    line must be refused (#557), or None.

    A missing line used to be no evidence at all, so a lossless archive whose
    db was deleted, left behind when only the .jxl files were copied, or
    written with --no-md5 was overwritten by any same-named JPEG — though it
    may be that photo's only copy. The archive is its own evidence: djxl
    --reconstruct_jpeg gives back the JPEG it holds. Refused when that ran and
    gave bytes other than jpeg_path's (another photo, or this one edited since:
    what a recorded line refuses too), and when jpeg_path cannot be hashed
    (fail closed). None when it gives jpeg_path back, or when it cannot run
    (djxl < 0.12, a damaged archive): that proves nothing either way.
    """
    rec = _jxl_reconstruct_md5(jxl_path)
    if rec is None:
        return None
    try:
        if md5_of_file(jpeg_path) == rec:
            return None
    except OSError as e:
        return f"could not hash {jpeg_path.name} ({e})"
    if decode:
        return ("the existing JPEG is not the one this JXL holds (no checksum "
                "recorded, and djxl --reconstruct_jpeg gives other bytes) — another "
                "photo, or a copy edited since")
    return ("it is the lossless archive of a DIFFERENT JPEG (no checksum recorded, "
            "and djxl --reconstruct_jpeg does not give this source back from it) — "
            "if this is the same photo edited since, delete the old .jxl first")


def _damaged_output_why(out) -> Optional[str]:
    """#619 (review 261110 item 1.3): an existing output this run refuses to
    write over may simply be BROKEN — what a crash or a power cut leaves.
    The refusal then said "another source" / "not the file the checksum was
    recorded for", and the user had to guess. Only the wording changes: the
    file is refused either way."""
    try:
        if out.exists() and not _verify_file_integrity(out):
            return ("the existing output is DAMAGED (it fails the integrity check — "
                    "truncated by a crash or a power cut?): check it, delete it and "
                    "re-run")
    except OSError:
        pass
    return None


def _foreign_overwrite_filter(pairs, decode_lossless, would_write, dry_run=False,
                              explicit_overwrite=False):
    """The half of _provenance_filter that runs when this run does NOT delete
    in a collapsing mode: drop every pair this run is about to OVERWRITE onto
    an existing output that is provably ANOTHER source's.

    "Without deletion an overwrite is recoverable" holds for the NEW source
    only: the existing output may be the archive of a photo an earlier run
    already deleted, and then a plain --sync/--overwrite run destroyed its only
    copy (#268's missing half; T3 of the 2026-10-08 audit, reproduced with a
    lossless JPEG archive). Provably another source's:
      * a provenance marker naming a different file (lossy outputs carry one);
      * a lossless JPEG archive (jbrd, no XMP by design) whose recorded
        checksum is not this JPEG's — another photo, or this one edited since;
      * on the JXL -> JPEG recovery, an existing JPEG whose bytes are not the
        checksum this JXL recorded;
      * the same two without a recorded checksum, when djxl --reconstruct_jpeg
        gives other bytes (#557, _lineless_archive_refusal). A dry run runs no
        reconstruction: it only says how many a real run will check.
      * a marker-less JPEG/PNG at the output of a lossy JXL decode, unless
        --overwrite was given (#616): every JPEG/PNG this decode writes carries
        jxlphoto-src, so a marker-less one is the camera's own file.
    No record either way on a LOSSY output (markerless) keeps the old
    behaviour. A hash that cannot be computed is a refusal (fail closed).

    would_write(src, out) -> bool is the run's own should_process; without it
    nothing is checked (direct callers that predate this keep their behaviour).
    """
    if would_write is None:
        return pairs, []
    targets = []
    for src, out in pairs:
        try:
            if out.exists() and would_write(src, out):
                targets.append((src, out))
        except OSError:
            continue
    if not targets:
        return pairs, []
    marks = _read_source_markers_batch(sorted({out for _s, out in targets}))
    refused = []
    lineless = []       # (src, out, jxl, jpeg, decode): judged by reconstruction below
    _marker_refusal = False
    _original_refusals = 0      # #616: a marker-less JPEG/PNG, not another source's
    for src, out in targets:
        why = None
        info = marks.get(str(out)) or {"src": None, "srcsum": None}
        if info.get("src") or info.get("srcsum"):
            if not _provenance_ok(info, src, PROVENANCE_CHECK):
                if OVERWRITE_GUARD:
                    why = ("it was made from a different source (its provenance "
                           "marker names another file)")
                    _marker_refusal = True
                else:
                    logger.warning(f"  OVERWRITE (overwrite guard off) | {out.name}: "
                                   f"its provenance marker names another source path")
        elif decode_lossless:
            stored = read_md5_db(src)
            if stored is not None:
                try:
                    if md5_of_file(out) != stored:
                        why = ("the existing JPEG is not the one this JXL holds "
                               "(checksum mismatch) — another photo, or a copy "
                               "edited since")
                except OSError as e:
                    why = f"could not hash the existing output ({e})"
            else:
                lineless.append((src, out, src, out, True))
        elif out.suffix.lower() == ".jxl" and has_jbrd_box(out):
            stored = read_md5_db(out)
            if stored is not None:
                try:
                    if md5_of_file(src) != stored:
                        why = ("it is the lossless archive of a DIFFERENT JPEG (its "
                               "recorded checksum does not match this source) — if "
                               "this is the same photo edited since, delete the old "
                               ".jxl first")
                except OSError as e:
                    why = f"could not hash the source ({e})"
            else:
                lineless.append((src, out, out, src, False))
        elif info.get("unread"):
            # X-A: a lossy output whose markers were never read is "cannot
            # tell", not "markerless" — it may name another photo, and once an
            # earlier run deleted that photo it is the only copy.
            if OVERWRITE_GUARD:
                why = ("its provenance markers could not be read (the exiftool batch "
                       "failed or stalled) — re-run once exiftool reads the folder "
                       "again")
            else:
                logger.warning(f"  OVERWRITE (overwrite guard off) | {out.name}: "
                               f"its provenance markers could not be read")
        elif (not explicit_overwrite and not _is_derivative()
              and src.suffix.lower() in JXL_EXTS
              and out.suffix.lower() in (".jpg", ".jpeg", ".png")):
            # #616: every JPEG/PNG this decode writes carries jxlphoto-src, so
            # a marker-less one is not ours — the camera's own JPEG beside the
            # master, say. The decoder's rule for an original TIFF master.
            why = ("it carries no jxlphoto-src marker, so it is not a file this "
                   "tool converted — it looks like an original JPEG/PNG. Convert "
                   "into another folder, or re-run with --overwrite to replace it "
                   "anyway")
            _original_refusals += 1
        if why:
            refused.append((src, out, _damaged_output_why(out) or why))
    if lineless and dry_run:
        logger.info(f" DRY | {len(lineless)} existing lossless archive(s) without a "
                    f"checksum line: a real run checks each with djxl --reconstruct_jpeg "
                    f"before overwriting it")
    elif lineless:
        logger.info(f"Overwrite guard: {len(lineless)} existing lossless archive(s) have "
                    f"no checksum line; checking each with djxl --reconstruct_jpeg...")
        for src, out, jxl, jpeg, decode in lineless:
            why = _lineless_archive_refusal(jxl, jpeg, decode)
            if why:
                refused.append((src, out, _damaged_output_why(out) or why))
    if not refused:
        return pairs, []
    _bad = {(os.path.normcase(str(s)), os.path.normcase(str(o))) for s, o, _w in refused}
    kept = [(s, o) for s, o in pairs
            if (os.path.normcase(str(s)), os.path.normcase(str(o))) not in _bad]
    _only_originals = _original_refusals == len(refused)
    if _only_originals:
        # #616: not another source's output — a JPEG/PNG this tool never
        # wrote (no jxlphoto-src). "--provenance content" cannot help here.
        logger.error(
            f"REFUSING {len(refused)} file(s): their output path holds a JPEG/PNG "
            f"this tool did not write (no jxlphoto-src marker — the camera's own "
            f"file, say). Converting would replace it.")
    else:
        logger.error(
            f"REFUSING {len(refused)} file(s): their output already exists and belongs "
            f"to a DIFFERENT source. Converting would overwrite it — and if an earlier "
            f"run deleted that source, the output is the only copy of the photo.")
    for _s, _o, _w in refused[:10]:
        logger.error(f"    {_s}")
        logger.error(f"      -> {_o} ({_w})")
    if len(refused) > 10:
        logger.error(f"    ... and {len(refused) - 10} more")
    logger.error(
        "  These were NOT converted and NOTHING was deleted. Rename them or write "
        "into another folder." +
        ("" if (decode_lossless or PROVENANCE_CHECK == "content" or _only_originals)
         else " If you MOVED the sources, re-run with --provenance content."))
    if _marker_refusal:
        for _l in _guard_refusal_hint(False):
            logger.error(_l)
    return kept, refused


def _provenance_filter(pairs, mode, decode_lossless=False,
                       output_arg=None, source_root=None, would_write=None,
                       dry_run=False, explicit_overwrite=False):
    """Drop pairs whose output already exists and came from a DIFFERENT source.

    The STRICT check (every existing output must prove it came from this
    source) runs in the folder-collapsing modes with DELETE_SOURCE armed.
    Every other run gets _foreign_overwrite_filter: only the outputs it is
    about to overwrite (would_write), refused only on positive evidence that
    they are another source's.

    decode_lossless: the JXL -> JPEG recovery path, whose output must stay
    byte-identical and therefore carries no marker. checksums.md5 holds the
    ORIGINAL JPEG's hash keyed by the JXL, so an existing output matching it
    came from this source — that is a stronger proof than any marker.

    Returns (kept_pairs, refused) — refused is [(src, out, why)].
    """
    if not (DELETE_SOURCE and _run_collapses_structure(mode, output_arg, source_root)):
        return _foreign_overwrite_filter(pairs, decode_lossless, would_write, dry_run,
                                         explicit_overwrite)
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
            _out_md5 = None
            if stored is not None:
                try:
                    _out_md5 = md5_of_file(out)
                except OSError as e:
                    refused.append((src, out, f"could not hash the existing output ({e})"))
                    continue
            if stored is not None and stored == _out_md5:
                # The JPEG matches the stored hash — but that hash is keyed by
                # the JXL's NAME. A swapped same-named JXL passes that check,
                # so bind it to the JXL's CONTENT before trusting the pair.
                if _jxl_binds_to_archived_jpeg(src, out, stored):
                    kept.append((src, out))
                    continue
                why = ("this JXL is not the file the existing JPEG was "
                       "recovered from (swapped or replaced same-named JXL)")
            else:
                why = ("no checksum to prove it" if stored is None
                       else "the existing JPEG is not the one this JXL holds")
        else:
            # A jbrd-carrying JXL gets NO XMP markers (they would break
            # djxl --reconstruct_jpeg), so marker provenance can never match.
            # Its provenance is the checksums.md5 db instead — same idea as
            # decode_lossless, but keyed by the OUTPUT's name: the db holds
            # the SOURCE JPEG's hash under the JXL's name. The content-binding
            # check additionally proves the JXL on disk is the file that
            # archived this source, not a swapped same-named replacement.
            if out.suffix.lower() == ".jxl" and has_jbrd_box(out):
                stored = read_md5_db(out)
                if stored is None and _adopt_reconstructed_archive(src, out, dry_run):
                    kept.append((src, out))       # #556: proven by reconstruction
                    continue
                if stored is None:
                    if dry_run:
                        why = ("no checksum to prove it (a real run checks whether "
                               "djxl --reconstruct_jpeg gives this JPEG back from it)")
                    elif not STORE_MD5:
                        why = "no checksum to prove it (--no-md5 records none)"
                    elif not _tool_at_least("djxl", 0, 12):
                        why = ("no checksum to prove it, and djxl < 0.12 cannot check "
                               "it by reconstruction")
                    else:
                        why = ("no checksum to prove it, and djxl --reconstruct_jpeg "
                               "does not give this JPEG back from it")
                else:
                    try:
                        _src_md5 = md5_of_file(src)
                    except OSError as e:
                        refused.append((src, out, f"could not hash the source ({e})"))
                        continue
                    if stored != _src_md5:
                        why = "the stored checksum does not match this source"
                    elif _jxl_binds_to_archived_jpeg(out, src, stored):
                        kept.append((src, out))
                        continue
                    else:
                        why = ("this JXL is not the file the checksum was recorded "
                               "for (swapped or replaced same-named JXL)")
            else:
                info = marks.get(str(out)) or {"src": None, "srcsum": None}
                if _provenance_ok(info, src, PROVENANCE_CHECK):
                    kept.append((src, out))
                    continue
                if info.get("unread"):
                    why = ("its provenance markers could not be read (the exiftool "
                           "batch failed or stalled) — re-run once exiftool reads "
                           "the folder again")
                else:
                    why = ("no provenance marker (written by an older version, or "
                           "not by this tool — an original JPEG/PNG?)"
                           if not (info.get("src") or info.get("srcsum"))
                           else "it was made from a different source")
        refused.append((src, out, _damaged_output_why(out) or why))
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
        logger.error(f"  Details: {GUARD_DOC_URL}")
    return kept, refused


def _lossy_skip_marks(results, src_map) -> dict:
    """Markers of every pre-existing output a lossy --delete-skipped could
    delete a source for, in one batch (T1 of the 2026-10-08 audit)."""
    if not DELETE_SKIPPED:
        return {}
    finals = []
    for result in results:
        if result[1] == "skipped":
            _sm = src_map.get(result[0])
            if _sm is not None and _sm[1] is not None:
                finals.append(_sm[1])
    return _read_source_markers_batch(finals) if finals else {}


def _lossy_skip_unproven(src_path, final_file, marks) -> bool:
    """True when an already-existing LOSSY output cannot prove it was made
    from `src_path` as it is now: its provenance marker is absent, unreadable
    or names another file, or the source's bytes no longer match the recorded
    srcsum. A jbrd JPEG archive is proven by the reconstruction test
    instead, so it is never judged here. Before this, a same-named JPEG from
    anywhere (the camera's own, next to the archived master) deleted the
    master JXL — reproduced in the 2026-10-08 audit."""
    if (final_file.suffix.lower() == '.jxl'
            and src_path.suffix.lower() in JPEG_EXTS
            and has_jbrd_box(final_file)):
        return False
    info = marks.get(str(final_file)) or {"src": None, "srcsum": None}
    # The path alone is the overwrite guard's question; deleting the source
    # also needs its CURRENT bytes to match the recorded srcsum — a source
    # replaced in place since that output was written is SKIPPED here (the
    # output exists), and the stale output deleted the only copy of it.
    return not _provenance_proves_archive(info, src_path, PROVENANCE_CHECK)


# #619: the delete gates' line for a source that is gone before its turn
# (another run or program deleted or moved it) — nothing to delete.
_SOURCE_GONE = ("SKIP (the source no longer exists — deleted or moved by another "
                "run or program while this one ran; nothing to delete)")

_LOSSY_SKIP_KEEP = ("KEEP (the existing output carries no provenance marker "
                    "MATCHING this source as it is now — absent, foreign, or the "
                    "source changed since that output was written — so it cannot "
                    "prove it is this file's conversion)")


def _plan_would_delete_source(pairs, smart_mode, reconvert_explicit) -> bool:
    """Would this plan even ATTEMPT a source deletion? The gates only delete
    for real conversions — or, under --delete-skipped, for the already-
    archived ones.

    The old confirmation ran before the plan was known: a no-TTY re-run of an
    already-archived folder (--delete-source, no --delete-skipped) was asked
    for a token it could not answer, exited 3, and would exit 3 FOREVER — with
    not a single deletion pending. Fail closed: any doubt (a file that would
    be processed, or a deletion-eligible skip) keeps the prompt.
    """
    for src, out in pairs:
        if should_process(src, out, smart_mode, reconvert_explicit):
            return True
        if DELETE_SKIPPED and out.exists():
            return True
    return False


def _delete_extras() -> dict:
    """Deletion counts for the run summary, so the wrapper's manifest recap can
    report the most destructive thing the tool does instead of staying silent.

    Empty without --delete-source: nothing was delete-eligible, and a nonzero
    "kept" from a staging anomaly would have read as a gate holding something
    back on a run that could not delete anything."""
    if not DELETE_SOURCE:
        return {}
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
    _planned_outputs.clear()        # run-scoped (#615)

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
    if not OVERWRITE_GUARD:
        logger.warning("Overwrite guard OFF: an existing output made from another "
                       "source path is overwritten (and logged) instead of refused.")

    # resolve_output_transcode has no rename parameters: only the lossy
    # convert path applies --rename-from/--rename-to. Accepting the flags
    # here without a word looked like a rename had been honored
    # (2026-09-21 audit, item 24).
    if getattr(args, "rename_from", "") or getattr(args, "rename_to", ""):
        logger.warning("--rename-from/--rename-to are ignored for JPEG->JXL "
                       "transcode paths (only the lossy convert path applies them)")

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
    _single_file = args.input.is_file()
    for f in files:
        out = resolve_output_transcode(f, args.mode, output_root, decode,
                                       single_file=_single_file)
        if out is None:
            continue  # Skip files outside _EXPORT for modes 6/7
        pairs.append((f, out))

    # Progress total must reflect modes 6/7 filtering, not the raw scan
    _counter["total"] = len(pairs)
    if len(pairs) != len(files):
        logger.info(f"Planned: {len(pairs)} (filtered by mode)")

    _abort_on_duplicate_outputs(pairs)
    if not args.dry_run:
        _sweep_orphan_temps(dst for _, dst in pairs)
        _record_planned_outputs(pairs)
    pairs, _refused = _provenance_filter(
        pairs, args.mode, decode_lossless=decode,
        output_arg=args.output, source_root=_prov_src_root(args),
        would_write=lambda s, o: should_process(s, o, smart_mode, reconvert_explicit),
        dry_run=args.dry_run)
    if _refused:
        _counter["total"] = len(pairs)

    if args.dry_run:
        # An output that already exists means the real run reports SKIP, not a
        # conversion (should_process is the same predicate the workers use).
        # Counting them as conversions made the dry run an upper bound the run
        # never reaches, and the wrapper sums these toplines (#409 family).
        _dry_pairs = [(f, out, should_process(f, out, smart_mode, reconvert_explicit))
                      for f, out in pairs]
        _n_skip = sum(1 for _f, _o, _go in _dry_pairs if not _go)
        for f, out, _go in _dry_pairs:
            if not _go:
                _lbl = "SKIP (up to date)" if smart_mode else "SKIP (exists)"
                logger.info(f" DRY | {_lbl} | {f.name}")
            else:
                logger.info(f" DRY | {f.name} -> {out}")
        logger.info(f"Dry run: {len(pairs) - _n_skip} files would be processed"
                    + (f"; {_n_skip} would be skipped (output already exists)."
                       if _n_skip else "."))
        # A dry run of a DELETE run must say so: the flag that destroys
        # originals was the one thing the simulation never mentioned (bug #267
        # — the preview below only existed in _process_file_group, i.e. cmd_auto).
        if DELETE_SOURCE:
            logger.warning(
                f"Dry run: --delete-source is ARMED. Up to {len(pairs) - _n_skip} "
                f"source file(s) would be DELETED, each only after its output is "
                f"written and passes the integrity check.")
        # Returning without this left emit_summary_json printing the UNTOUCHED
        # default (dry_run=false, ok=0, log=""), so the wrapper's recap showed a
        # simulation as a finished real run with zeros and never printed its
        # [DRY RUN] banner. The encoder and decoder report the planned count.
        if _refused:
            logger.warning(f"Dry run: {len(_refused)} pair(s) would be REFUSED by "
                           f"the provenance check (see above); the real run fails "
                           f"those files.")
        record_summary(ok=len(pairs) - _n_skip, overwritten=0, skipped=_n_skip,
                       errors=len(_refused),
                       log_file=log_file, dry_run=True)
        return (0, False)

    # Create the mode-2 output dir only for real runs (dry-run must not write)
    if args.mode == 2 and not args.input.is_file():
        output_root.mkdir(parents=True, exist_ok=True)

    # Group by output folder
    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))

    # Charged for EVERY mode: deletion is a separate opt-in from the layout,
    # but only when the plan would even attempt a deletion (a re-run of an
    # already-archived folder with no TTY used to be asked a token it could
    # not answer and exited 3 forever — see _plan_would_delete_source).
    if DELETE_SOURCE and _plan_would_delete_source(pairs, smart_mode, reconvert_explicit):
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
    _source_identity.clear()
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
                           output_root: Path = None, decode: bool = False,
                           single_file: bool = False) -> Path:
    stem = src_path.stem
    if rename_from and rename_from in stem:
        stem = stem.replace(rename_from, rename_to, 1)

    # Determine folder names based on direction
    conv_folder = RECOVERED_JPEG_FOLDER if decode else CONVERTED_JXL_FOLDER
    sibling_folder = JPEG_SIBLING_FOLDER if decode else JXL_SIBLING_FOLDER
    sfx_from = JXL_SUFFIX_TO_REPLACE if decode else JPEG_SUFFIX_TO_REPLACE
    sfx_to = JPEG_SUFFIX_REPLACE_DEC if decode else JXL_SUFFIX_REPLACE
    exp_out = EXPORT_JPEG_FOLDER if decode else EXPORT_JXL_FOLDER

    # Anchor for the modes-4/5 outside-the-tree warning. Callers disagree on
    # what output_root means for a single-file run: cmd_convert passes the
    # file's PARENT, cmd_auto passes the FILE itself — cover both (the
    # single_file flag is what makes the anchor climb at all; item 29).
    _warn_anchor = None
    if output_root:
        _root = Path(output_root)
        if single_file:
            _warn_anchor = _root.parent.parent if _root.is_file() else _root.parent
        else:
            _warn_anchor = _root

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
        if _warn_anchor and not _is_relative_to(result, _warn_anchor):
            logger.warning(f"Output outside input tree: {src_path.name} -> {result}")
        return result
    elif mode == 5:
        # Sibling folder (e.g., JXL_jpeg/ or JPEG_recovered/) — aligned with
        # encoder/decoder mode 5
        result = src_path.parent.parent / sibling_folder / f"{stem}.{ext}"
        if _warn_anchor and not _is_relative_to(result, _warn_anchor):
            logger.warning(f"Output outside input tree: {src_path.name} -> {result}")
        return result
    elif mode in (6, 7):
        # Export marker modes - only process files INSIDE export marker folder
        # Match only path *directory* parts (parts[:-1]); a file whose own
        # name happens to start/end with the marker is not an anchor.
        parts = src_path.parts
        marker_lower = EXPORT_MARKER.lower()
        # Match folders starting or ending with EXPORT_MARKER case-insensitively
        export_idx = _marker_anchor_index(parts[:-1], output_root, marker_lower)
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
    # Never write under the final name without staging (see
    # encode_one_transcode): uuid temp beside the final + atomic os.replace
    # after the integrity check. ".tmp" suffix per the item-26 rule: no
    # orphan beside the final is adoptable as real input; cjxl defaults to
    # the JXL writer for an unknown extension.
    _promote_local = write_path == final_path
    if _promote_local:
        write_path = final_path.parent / f"{uuid.uuid4().hex}_{final_path.name}.tmp"
    _pre_identity = _capture_output_identity(write_path, final_path)

    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)

        # Build cjxl command
        cmd = (["cjxl", str(src_path), str(write_path), "--effort", str(effort), "-d", str(distance)]
               + _metadata_boxes_flag())
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
        r = _run_captured(cmd, CODEC_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"cjxl: {_stderr_tail(r.stderr)}")

        src_md5 = md5_of_file(src_path) if STORE_MD5 else None
        # Preserve EXIF/XMP/IPTC metadata that cjxl may drop in lossy mode —
        # but NEVER into a jbrd-carrying JXL. _copy_metadata's
        # `-tagsfromfile -xmp:all` REWRITES the XMP inside the container, and
        # on a jbrd container that re-serialization breaks
        # `djxl --reconstruct_jpeg` (exiftool reorders the rdf:Description
        # blocks): the original JPEG silently stops being bit-exactly
        # recoverable while every structural check still passes — the exact
        # v2.0.0+ data-loss class this box-guard exists for (reproduced in the
        # 2026-09-21 audit, item 1). At d=0 on a JPEG input cjxl keeps the
        # jbrd box AND the original metadata, so the copy is not needed there;
        # at d>0 there is no jbrd and the copy runs as before.
        # Provenance markers live under the same guard: the appended XMP
        # breaks reconstruction the same way (see encode_one_transcode).
        # A failure here is an ERROR for the file (#571): the except below
        # removes the output and the source stays. It used to be logged at
        # DEBUG, and --delete-source then deleted a source whose output had
        # lost its IPTC or its provenance markers.
        _pixel_reencode = False
        if not has_jbrd_box(write_path):
            try:
                _write_converted_metadata(src_path, write_path)
            except RuntimeError as _e_meta:
                # #639: cjxl copied the JPEG's EXIF into the container as a
                # block, and when that EXIF is malformed (a broken IFD1) exiftool
                # refuses every later write to the file. One retry: the file is
                # encoded again without that copy and the EXIF comes fresh from
                # the JPEG through exiftool. A second failure is the file's
                # error; never `-m` (#571), nothing is deleted.
                if not is_jpeg_input:
                    raise
                # cjxl keeps a JPEG's EXIF Orientation in the CODESTREAM, and
                # `-x strip=exif` drops it with the EXIF: the retried JXL of a
                # rotated JPEG would show the picture sideways in a JXL viewer
                # (its Exif box would still say "Rotate 90 CW"). The first
                # encode still has the codestream orientation: a rotated JPEG
                # is not retried (review of #639). Only a PROVEN upright
                # first encode is retried: an orientation that cannot be read
                # is "cannot tell", not "upright" (second review).
                _o_first = _jxl_orientation(write_path)
                if _o_first != 1:
                    _how = (f"the JPEG is rotated (EXIF Orientation {_o_first})"
                            if _o_first in _UNROTATE_MAGICK else
                            "the orientation of the first encode could not be read")
                    raise RuntimeError(
                        f"{_e_meta} | {_how} and its EXIF is malformed; a second encode "
                        f"without that EXIF would lose the rotation. "
                        f"--force-transcode (lossless, bit-exact) keeps both") from None
                logger.info(f"  >metadata copied again without the EXIF the codec "
                            f"wrote | {src_path.name} | first try: {_e_meta}")
                _delete_partial_if_written(write_path, final_path, _pre_identity)
                r = _run_captured(cmd + _CJXL_STRIP_EXIF, CODEC_TIMEOUT)
                if r.returncode != 0:
                    raise RuntimeError(f"cjxl: {_stderr_tail(r.stderr)}")
                try:
                    _write_converted_metadata(src_path, write_path)
                    _verify_exif_kept(src_path, write_path)
                except RuntimeError as _e_retry:
                    raise RuntimeError(_with_makernote_hint(str(_e_retry))) from None
            _pixel_reencode = is_jpeg_input

        # Reorder boxes for IrfanView compatibility — must be the LAST mutation,
        # otherwise exiftool re-appends metadata boxes after the codestream.
        reorder_jxl_boxes(write_path)

        # Validate EVERY successful output (rc=0 does not guarantee a
        # well-formed file).
        if not _verify_file_integrity(write_path):
            raise RuntimeError("cjxl returned 0 but the output failed the integrity check")

        # A d=0 --force-convert JPEG yields a jbrd container, which by design
        # carries NO XMP markers (they break djxl --reconstruct_jpeg): its
        # provenance is the checksums.md5 db instead. Record the same entries
        # encode_one_transcode records when STORE_MD5 — otherwise the second
        # run in a collapsing mode with --delete-source fails closed with the
        # misleading "no checksum to prove it (was it written with --no-md5?)".
        jxl_md5 = None
        jbrd_lines = bool(STORE_MD5 and src_md5 and has_jbrd_box(write_path))
        if jbrd_lines:
            # Hashed AFTER reorder_jxl_boxes + the integrity check, so it is
            # the exact bytes that reach the destination.
            jxl_md5 = md5_of_file(write_path)
            if not _promote_local:
                checksum_path = write_path.parent / final_path.name
                store_md5_db(checksum_path, src_md5)
                store_jxl_self_hash_db(checksum_path, jxl_md5)

        if _promote_local:
            _swap_into_place(write_path, final_path)
            if jbrd_lines:
                # After the swap, never before it (#556; see encode_one_transcode).
                _file_checksum_lines(final_path, src_md5, jxl_md5)

        n, total = next_count()
        label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {src_path.name} -> {final_path.name}")
        # #641: a pixel re-encode holds the first image of the JPEG only. An
        # HDR gain map after it is not in this JXL: said per file, and the
        # delete gates keep such a JPEG (_gain_map_keep_reason).
        if _pixel_reencode:
            _note_gain_map_left_out(src_path)
        # result[4] carries the JXL self-hash so process_group_convert can
        # file it next to the source hash at the destination (staging runs).
        return (str(src_path), "reconvert" if overwritten else "ok", str(final_path),
                src_md5, jxl_md5)
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


# ImageMagick operations that put upright pixels back into the order a JPEG
# with EXIF Orientation n stored them in — the inverse of each orientation,
# every one an exact permutation (checked against -auto-orient, 2026-11-10).
_UNROTATE_MAGICK = {2: ["-flop"], 3: ["-rotate", "180"], 4: ["-flip"],
                    5: ["-transpose"], 6: ["-rotate", "270"],
                    7: ["-transverse"], 8: ["-rotate", "90"]}


def _xmp_box_mentions(jxl_path, needle: bytes):
    """Whether the plain XMP box of a JXL holds `needle` (True/False), read
    in Python; None when only exiftool can tell (a Brotli-compressed XMP box,
    an XMP box over 16 MB, a file this cannot walk). A bare codestream has no
    XMP: False. Box headers are read and skipped; only the XMP is read."""
    try:
        with open(jxl_path, "rb") as f:
            first = f.read(12)
            if first[:2] == b"\xff\x0a":
                return False
            if first != _JXL_SIGNATURE:
                return None
            end = f.seek(0, 2)
            pos, found = 12, False
            while pos + 8 <= end:
                f.seek(pos)
                head = f.read(8)
                size, typ, hdr = int.from_bytes(head[:4], "big"), head[4:8], 8
                if size == 1:
                    size, hdr = int.from_bytes(f.read(8), "big"), 16
                elif size == 0:
                    size = end - pos
                if size < hdr or pos + size > end:
                    return None
                if typ == b"brob" and f.read(4) == b"xml ":
                    return None
                if typ == b"xml ":
                    if size - hdr > 16 * 1024 * 1024:
                        return None
                    found = found or needle in f.read(size - hdr)
                pos += size
            return found if pos == end else None
    except OSError:
        return None


def _stored_orientation(jxl_path):
    """The EXIF Orientation (2-8) of the JPEG this JXL's photo came from, or
    None: the codestream's own (a JXL made straight from a JPEG), else the
    jxlphoto-orientation token a recompression left (ORIENTATION_PREFIX).
    None when it is 1, unknown or unreadable — the output then stays upright,
    which is always a correct picture."""
    o = _jxl_orientation(jxl_path)
    if o in _UNROTATE_MAGICK:
        return o
    # Only a recompressed copy carries the token, and most files say so
    # without an exiftool run (one spawn per JPEG output, ~0.2 s).
    if _xmp_box_mentions(jxl_path, ORIENTATION_PREFIX.encode("ascii")) is False:
        return None
    try:
        _creator, tokens = _read_creator_and_relation(jxl_path)
    except Exception:
        return None
    for t in tokens:
        if t.startswith(ORIENTATION_PREFIX):
            v = t[len(ORIENTATION_PREFIX):]
            return int(v) if v.isdigit() and int(v) in _UNROTATE_MAGICK else None
    return None


_reduced_by_integer_decode = _common._reduced_by_integer_decode


_djxl_icc_args = _common._djxl_icc_args


_png_has_alpha = _common._png_has_alpha


_icc_a2b_only = _common._icc_a2b_only


_decoded_in_original_space = _common._decoded_in_original_space


_xmp_icc_from_creator_tool = _common._xmp_icc_from_creator_tool


_carried_icc = _common._carried_icc


def _read_creator_and_relation(*a, **k): return _common._read_creator_and_relation(_G, *a, **k)


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
            if not icc_path.exists() or icc_path.stat().st_size < 128:
                profile_bytes = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
                # Atomic write: a crash mid-write used to leave a truncated
                # profile cached under this stable name, failing every future
                # --to-srgb run until someone deleted it by hand.
                _part = icc_path.with_name(icc_path.name + f".{os.getpid()}.part")
                try:
                    _part.write_bytes(profile_bytes)
                    os.replace(str(_part), str(icc_path))
                finally:
                    try:
                        _part.unlink()
                    except OSError:
                        pass
            _srgb_icc_cache = str(icc_path)
        except Exception:
            _srgb_icc_cache = False  # do not retry every file
    return _srgb_icc_cache or None


# Adobe RGB (1998)-compatible matrix/TRC profile: the SAME 500 bytes
# jxl_recompressor._adobe_rgb_icc_bytes() generates (0 difference from Adobe's
# own AdobeRGB1998.icc in 8 bits, intents 0 and 1; D65 media white, so cjxl
# records the real primaries). Stored as bytes rather than regenerated because
# the generator needs numpy, which this script does not otherwise require;
# tests/test_transcoder_adobergb.py pins it byte-for-byte to the recompressor.
_ADOBE_RGB_ICC_B64 = (
    "AAAB9GxjbXMCEAAAbW50clJHQiBYWVogAAAAAAAAAAAAAAAAYWNzcE1TRlQAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAPbWAAEAAAAA0y0AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAJZGVzYwAAAPAAAAB+Y3BydAAAAXAAAAAhd3RwdAAAAZQAAAAUclhZ"
    "WgAAAagAAAAUZ1hZWgAAAbwAAAAUYlhZWgAAAdAAAAAUclRSQwAAAeQAAAAOZ1RSQwAAAeQAAAAO"
    "YlRSQwAAAeQAAAAOZGVzYwAAAAAAAAAkQWRvYmVSR0IxOTk4LWNvbXBhdGlibGUgKGp4bC1waG90"
    "bykAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB0ZXh0AAAAAE5vIGNvcHlyaWdodCwgdXNlIGZyZWVs"
    "eQAAAABYWVogAAAAAAAA81EAAQAAAAEWzFhZWiAAAAAAAACcGAAAT6UAAAT8WFlaIAAAAAAAADSN"
    "AACgLAAAD5VYWVogAAAAAAAAJjEAABAvAAC+m2N1cnYAAAAAAAAAAQIzAAA="
)
_adobergb_icc_cache = None
_adobergb_icc_lock = threading.Lock()

# --icc-profile spellings that mean a built-in profile (case-insensitive).
_ICC_ALIASES = {"srgb": "sRGB", "adobergb": "AdobeRGB", "adobe": "AdobeRGB",
                "adobergb1998": "AdobeRGB"}


def _get_adobergb_icc_path() -> str:
    """Stable path of the built-in Adobe RGB (1998)-compatible profile, written
    once (atomically, under a lock) like the sRGB one."""
    global _adobergb_icc_cache
    if _adobergb_icc_cache is not None:
        return _adobergb_icc_cache
    with _adobergb_icc_lock:
        if _adobergb_icc_cache is not None:
            return _adobergb_icc_cache
        data = base64.b64decode("".join(_ADOBE_RGB_ICC_B64))
        base = Path(tempfile.gettempdir()) if TEMP_DIR is None else Path(TEMP_DIR)
        icc_path = base / "jxl_photo_AdobeRGB1998-compatible.icc"
        if not icc_path.exists() or icc_path.read_bytes() != data:
            _part = icc_path.with_name(icc_path.name + f".{os.getpid()}.part")
            try:
                _part.write_bytes(data)
                os.replace(str(_part), str(icc_path))
            finally:
                try:
                    _part.unlink()
                except OSError:
                    pass
        _adobergb_icc_cache = str(icc_path)
    return _adobergb_icc_cache


_png_iccp = _common._png_iccp


# ImageMagick writes an sRGB chunk INSTEAD of the iCCP when the profile is one
# of the standard sRGB ones it recognises; with this it writes the profile.
_MAGICK_KEEP_ICCP = ["-define", "png:preserve-iCCP=true"]


def _source_profile_args(jxl_path: Path, png_path: Path, tmp_dir: Path) -> tuple:
    """(magick args, source profile path) describing the decoded pixels.

    djxl writes a PNG with an sRGB CHUNK and no iCCP for an sRGB-encoded JXL,
    and `magick -profile X` then ASSIGNS X instead of converting from sRGB:
    the colours shift silently (trap B1, measured 2026-09-25 — output
    identical to a plain assignment, 35.7 dB away from the correct
    conversion). The source profile is therefore assigned EXPLICITLY before
    any target profile, with the same precedence the recompressor uses:
    the XMP ICC (_carried_icc: the jxlphoto-iccprofile token, else the
    CreatorTool ICC of older files) > the decoded PNG's own iCCP > an sRGB
    chunk > refuse to guess.

    Returns the path of the profile that describes the pixels; a caller that
    sharpens in Lab loses it (trap B2) and must re-assign this one.
    """
    creator, _tokens = _read_creator_and_relation(jxl_path)
    icc = _carried_icc(creator, _tokens)
    if icc:
        path = tmp_dir / "src.icc"
        path.write_bytes(icc)
        return (["+profile", "*", "-profile", str(path)], path)
    chunks = _png_chunk_types(png_path)
    if "iCCP" in chunks:
        # The profile djxl wrote, read from the chunk and assigned explicitly
        # (#635): reading a PNG, ImageMagick drops an iCCP it recognises as a
        # standard sRGB profile, so it could neither hand it back ("no color
        # profile is available") nor convert from it — a target profile would
        # have been ASSIGNED (the trap of the sRGB chunk below). For any other
        # profile the assignment changes nothing.
        icc = _png_iccp(png_path)
        if not icc:
            raise RuntimeError("the decoded PNG's iCCP profile could not be read — "
                               "refusing to guess the source colour space")
        path = tmp_dir / "src_iccp.icc"
        path.write_bytes(icc)
        return (["+profile", "*", "-profile", str(path)], path)
    if "sRGB" in chunks:
        path = _get_srgb_icc_path()
        if not path:
            raise RuntimeError("the decoded PNG says sRGB but no sRGB profile is "
                               "available (Pillow missing?) — refusing to guess")
        return (["+profile", "*", "-profile", str(path)], Path(path))
    raise RuntimeError("cannot tell the source colour space (no XMP ICC, no iCCP, "
                       "no sRGB chunk) — refusing to guess")


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Chunks that describe a PNG's colour space; an iCCP replaces all of them.
# cICP too: PNG 3rd edition gives it precedence over iCCP, so a reader that
# knows it would keep showing the old space (djxl 0.12 writes sRGB/gAMA/cHRM
# for the SDR masters measured on 2026-11-04, not cICP; 261104 review, S2).
_PNG_COLOUR_CHUNKS = (b"iCCP", b"sRGB", b"gAMA", b"cHRM", b"cICP")
_JPEG_ICC_HEADER = b"ICC_PROFILE\x00"
_JPEG_ICC_CHUNK = 65519        # 65535 - 2 (length) - 14 (header + seq + count)


def _png_chunk(name: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + name + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF))


def _png_replace_icc(path: Path, icc: bytes) -> bool:
    """Make `icc` the colour profile of the PNG at `path`, pixels untouched:
    its iCCP/sRGB/gAMA/cHRM chunks go, one iCCP goes right after the IHDR.
    Streamed through a temp beside the file + os.replace. False (file left
    alone) when it already carries exactly that profile."""
    path = Path(path)
    with open(path, "rb") as f:
        if f.read(8) != _PNG_SIGNATURE:
            raise RuntimeError(f"not a PNG: {path.name}")
        while True:
            head = f.read(8)
            if len(head) < 8:
                break
            n, name = struct.unpack(">I", head[:4])[0], head[4:8]
            if name == b"iCCP":
                data = f.read(n)
                try:
                    if zlib.decompress(data[data.index(b"\x00") + 2:]) == icc:
                        return False
                except (ValueError, zlib.error):
                    pass
                break
            if name == b"IDAT":
                break
            f.seek(n + 4, 1)
    tmp = path.parent / f"{uuid.uuid4().hex}_{path.name}.tmp"
    try:
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            dst.write(src.read(8))
            while True:
                head = src.read(8)
                if len(head) < 8:
                    break
                n, name = struct.unpack(">I", head[:4])[0], head[4:8]
                if name in _PNG_COLOUR_CHUNKS:
                    src.seek(n + 4, 1)
                    continue
                dst.write(head)
                shutil.copyfileobj(_LimitedReader(src, n + 4), dst)
                if name == b"IHDR":
                    dst.write(_png_chunk(b"iCCP", b"ICC Profile\x00\x00"
                                         + zlib.compress(icc)))
        os.replace(str(tmp), str(path))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return True


class _LimitedReader:
    """read() at most `n` bytes from `f` (for shutil.copyfileobj)."""

    def __init__(self, f, n):
        self.f, self.left = f, n

    def read(self, size=-1):
        if self.left <= 0:
            return b""
        size = self.left if size is None or size < 0 else min(size, self.left)
        data = self.f.read(size)
        self.left -= len(data)
        return data


def _jpeg_header_segments(f):
    """[(marker, payload)] of a JPEG's segments up to (not including) SOS, the
    file positioned at the SOS marker. Raises on anything but a JPEG."""
    if f.read(2) != b"\xff\xd8":
        raise RuntimeError("not a JPEG")
    segs = []
    while True:
        m = f.read(2)
        if len(m) < 2 or m[0] != 0xFF:
            raise RuntimeError("malformed JPEG marker")
        while m[1] == 0xFF:                       # fill bytes
            m = m[:1] + f.read(1)
        if m[1] == 0xDA:                          # SOS: entropy data follows
            f.seek(-2, 1)
            return segs
        if m[1] in (0x01,) or 0xD0 <= m[1] <= 0xD7:
            segs.append((m[1], b""))
            continue
        ln = struct.unpack(">H", f.read(2))[0]
        segs.append((m[1], f.read(ln - 2)))


def _jpeg_components(path: Path):
    """Number of colour components of a JPEG (from its SOFn), or None."""
    try:
        with open(path, "rb") as f:
            for marker, payload in _jpeg_header_segments(f):
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    return payload[5] if len(payload) > 5 else None
    except (OSError, RuntimeError, struct.error):
        return None
    return None


def _jpeg_replace_icc(path: Path, icc: bytes) -> bool:
    """Make `icc` the colour profile of the JPEG at `path`, the image data
    untouched: its APP2 ICC_PROFILE segments go, new ones go right after the
    leading APP0/APP1 segments. Temp beside the file + os.replace. False (file
    left alone) when it already carries exactly that profile."""
    path = Path(path)
    with open(path, "rb") as f:
        segs = _jpeg_header_segments(f)
    old = sorted((p[12], p[14:]) for m, p in segs
                 if m == 0xE2 and p[:12] == _JPEG_ICC_HEADER and len(p) >= 14)
    if old and b"".join(d for _s, d in old) == icc:
        return False
    pieces = [icc[i:i + _JPEG_ICC_CHUNK] for i in range(0, len(icc), _JPEG_ICC_CHUNK)]
    if len(pieces) > 255:
        raise RuntimeError("ICC profile too large for a JPEG")
    app2 = [b"\xff\xe2" + struct.pack(">H", 2 + 14 + len(d)) + _JPEG_ICC_HEADER
            + bytes((i + 1, len(pieces))) + d for i, d in enumerate(pieces)]
    tmp = path.parent / f"{uuid.uuid4().hex}_{path.name}.tmp"
    try:
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            segs = _jpeg_header_segments(src)        # src now at the SOS
            dst.write(b"\xff\xd8")
            placed = False
            for marker, payload in segs:
                if marker == 0xE2 and payload[:12] == _JPEG_ICC_HEADER:
                    continue
                if not placed and marker not in (0xE0, 0xE1):
                    dst.write(b"".join(app2))
                    placed = True
                if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                    dst.write(bytes((0xFF, marker)))
                else:
                    dst.write(bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2)
                              + payload)
            if not placed:
                dst.write(b"".join(app2))
            shutil.copyfileobj(src, dst)
        os.replace(str(tmp), str(path))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return True


# What djxl writes into a PNG for the EXIF/XMP/IPTC of the JXL: text chunks
# with these keywords, not the standard eXIf / XML:com.adobe.xmp.
_DJXL_PNG_PROFILES = (b"Raw profile type exif\x00", b"Raw profile type xmp\x00",
                      b"Raw profile type iptc\x00", b"Raw profile type APP1\x00",
                      b"Raw profile type 8bim\x00")
_PNG_TEXT_CHUNKS = (b"tEXt", b"zTXt", b"iTXt")
_JPEG_APP1_METADATA = (b"Exif\x00\x00", b"http://ns.adobe.com/xap/1.0/\x00",
                       b"http://ns.adobe.com/xmp/extension/\x00")


def _drop_djxl_png_profiles(path: Path) -> bool:
    """Remove the 'Raw profile type exif/xmp/...' text chunks djxl wrote into
    the PNG at `path` (#630), pixels and every other chunk untouched: exiftool
    fails on a PNG that holds such an EXIF chunk and no XMP one (`IFD0 pointer
    references previous IFD0 directory`), and a PNG it could write kept two
    EXIF blocks. The metadata copy from the JXL then writes one standard eXIf
    and one XMP. Temp beside the file + os.replace. True when a chunk went."""
    path = Path(path)
    found = False
    with open(path, "rb") as f:
        if f.read(8) != _PNG_SIGNATURE:
            raise RuntimeError(f"not a PNG: {path.name}")
        while not found:
            head = f.read(8)
            if len(head) < 8:
                break
            n, name = struct.unpack(">I", head[:4])[0], head[4:8]
            key = f.read(min(n, 32)) if name in _PNG_TEXT_CHUNKS else b""
            found = key.startswith(_DJXL_PNG_PROFILES)
            f.seek(n + 4 - len(key), 1)
    if not found:
        return False
    tmp = path.parent / f"{uuid.uuid4().hex}_{path.name}.tmp"
    try:
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            dst.write(src.read(8))
            while True:
                head = src.read(8)
                if len(head) < 8:
                    break
                n, name = struct.unpack(">I", head[:4])[0], head[4:8]
                key = src.read(min(n, 32)) if name in _PNG_TEXT_CHUNKS else b""
                if key.startswith(_DJXL_PNG_PROFILES):
                    src.seek(n + 4 - len(key), 1)
                    continue
                dst.write(head + key)
                shutil.copyfileobj(_LimitedReader(src, n + 4 - len(key)), dst)
        os.replace(str(tmp), str(path))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return True


def _jpeg_codec_metadata(marker: int, payload: bytes) -> bool:
    """An APP1 Exif/XMP or APP13 Photoshop (IPTC) segment."""
    return ((marker == 0xE1 and payload.startswith(_JPEG_APP1_METADATA))
            or (marker == 0xED and payload.startswith(b"Photoshop 3.0\x00")))


def _drop_jpeg_codec_metadata(path: Path) -> bool:
    """Remove the EXIF, XMP and IPTC segments a codec copied into the JPEG at
    `path` (#631), image data and every other segment (JFIF, ICC, Adobe)
    untouched: djxl and ImageMagick carry a malformed EXIF over as a block,
    and exiftool then cannot rewrite it. Temp beside the file + os.replace.
    True when a segment went."""
    path = Path(path)
    with open(path, "rb") as f:
        segs = _jpeg_header_segments(f)
    if not any(_jpeg_codec_metadata(m, p) for m, p in segs):
        return False
    tmp = path.parent / f"{uuid.uuid4().hex}_{path.name}.tmp"
    try:
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            segs = _jpeg_header_segments(src)        # src now at the SOS
            dst.write(b"\xff\xd8")
            for marker, payload in segs:
                if _jpeg_codec_metadata(marker, payload):
                    continue
                if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                    dst.write(bytes((0xFF, marker)))
                else:
                    dst.write(bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2)
                              + payload)
            shutil.copyfileobj(src, dst)
        os.replace(str(tmp), str(path))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return True


def _relabel_with_carried_icc(jxl_path: Path, out_path: Path, is_png: bool) -> bool:
    """Give a direct djxl JPEG/PNG the profile the master carries in XMP
    (_carried_icc), pixels untouched (#568). Only when djxl decoded in the
    file's own space — the caller checks — and when the profile fits the
    output's channels (an RGB profile on a grey file is invalid). True when
    the output was relabelled."""
    creator, tokens = _read_creator_and_relation(jxl_path)
    icc = _carried_icc(creator, tokens)
    if not icc or len(icc) < 20:
        return False
    grey = (_png_is_grayscale(out_path) if is_png
            else _jpeg_components(out_path) == 1)
    if icc[16:20] != (b"GRAY" if grey else b"RGB "):
        return False
    return (_png_replace_icc if is_png else _jpeg_replace_icc)(out_path, icc)


def _verify_profile_in_output(path: Path, is_png: bool) -> None:
    """Fail closed when a colour-converted output lost its ICC profile.

    ImageMagick can write the converted image without the profile (the
    Lab-sharpening and -strip traps A1/A2/B2), and without it the file is
    silently labelled as sRGB by every reader.
    """
    if is_png:
        if "iCCP" not in _png_chunk_types(path):
            # Same wording as the recompressor's _derive_pixels.
            raise RuntimeError("ImageMagick wrote the converted image without its ICC "
                               "profile — refusing to encode it as the wrong colour space")
        return
    r = _run_exiftool_argfile(
        ["-j", "-s", "-s", "-ICC_Profile:ProfileDescription", str(path)], timeout=EXIFTOOL_TIMEOUT)
    if not r.stdout:
        raise RuntimeError(f"exiftool could not read the output profile (rc={r.returncode})")
    entry = json.loads(r.stdout)[0]
    if not entry.get("ProfileDescription"):
        raise RuntimeError("ImageMagick wrote the converted image without its ICC "
                           "profile — refusing to deliver it as the wrong colour space")


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
    if output_icc == "AdobeRGB":
        return ["-profile", _get_adobergb_icc_path()] + extra
    return ["-profile", output_icc] + extra


_png_is_grayscale = _common._png_is_grayscale


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


def _png_bit_depth(png_path: Path):
    """Bit depth from the IHDR (byte 24), or None. Only 25 bytes are read."""
    try:
        with open(png_path, "rb") as f:
            head = f.read(25)
    except OSError:
        return None
    if len(head) < 25 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return head[24]


# What says "this JPEG carries an HDR gain map": a second, smaller image after
# the picture that tells an HDR screen how much brighter each area is (Android's
# Ultra HDR, ISO 21496-1, Apple's HDR gain map). Only a pixel re-encode loses
# it: cjxl reads the first image of the file and nothing else (#641).
_GAIN_MAP_MARKS = (
    (b"urn:iso:std:iso:ts:21496:-1", "ISO 21496-1"),
    (b"http://ns.adobe.com/hdr-gain-map/1.0/", "Ultra HDR"),
    (b"urn:com:apple:photo:2020:aux:hdrgainmap", "Apple"),
    (b"http://ns.apple.com/HDRGainMap/1.0/", "Apple"),
)


def _jpeg_gain_map(path: Path):
    """The kind of HDR gain map a JPEG carries ("Ultra HDR", "ISO 21496-1",
    "Apple"), or None. The APP1/APP2 segments of the first image are read; the
    rest of the file only when it announces further images (an MPF index) and
    its own header named no gain map — Apple describes its gain map in the
    second image. A camera's MPO (preview images) has none of the marks and is
    not one. Raises OSError when the file cannot be read: "cannot tell" is the
    caller's to treat as a reason to keep the file."""
    more_images = False
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            return None
        while True:
            head = f.read(4)
            if len(head) < 4 or head[0] != 0xFF:
                break
            marker = head[1]
            if marker == 0xFF:                        # a fill byte
                f.seek(-3, 1)
                continue
            if marker == 0x01 or 0xD0 <= marker <= 0xD8:   # no length
                f.seek(-2, 1)
                continue
            if marker in (0xDA, 0xD9):                # image data, end of image
                break
            size = struct.unpack(">H", head[2:4])[0]
            if size < 2:
                break
            if marker in (0xE1, 0xE2):
                data = f.read(size - 2)
                if data.startswith(b"MPF\x00"):
                    more_images = True
                for mark, kind in _GAIN_MAP_MARKS:
                    if mark in data:
                        return kind
            else:
                f.seek(size - 2, 1)
        if not more_images:
            return None
        f.seek(0)
        keep = max(len(m) for m, _k in _GAIN_MAP_MARKS)
        tail = b""
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                return None
            buf = tail + chunk
            for mark, kind in _GAIN_MAP_MARKS:
                if mark in buf:
                    return kind
            tail = buf[-keep:]


def _gain_map_keep_reason(src_path: Path, final_file: Path):
    """Why a JPEG converted to a JXL without a jbrd box (a pixel re-encode:
    --force-convert --from-jpeg above distance 0) must not be deleted, or None
    (#641). cjxl encodes the first image of the JPEG; an HDR gain map after it
    is not in the JXL, so the JPEG is the only copy of it. The lossless
    transcode keeps the whole file and is not judged here (its JXL has the
    jbrd box). A JPEG that cannot be read for the check is kept too."""
    if (src_path.suffix.lower() not in JPEG_EXTS or final_file is None
            or final_file.suffix.lower() != ".jxl"):
        return None
    try:
        if has_jbrd_box(final_file):
            return None
        kind = _jpeg_gain_map(src_path)
    except OSError:
        return "KEEP (could not check the JPEG for an HDR gain map)"
    if kind:
        return (f"KEEP (the JPEG carries an HDR gain map ({kind}) this JXL does not "
                f"hold — only the lossless transcode, --force-transcode, keeps it)")
    return None


def _note_gain_map_left_out(src_path: Path) -> None:
    """One warning per converted JPEG whose HDR gain map did not reach the JXL
    (#641): the picture is complete, the HDR rendering is not in it."""
    try:
        kind = _jpeg_gain_map(src_path)
    except OSError:
        return
    if kind:
        logger.warning(f"  >HDR gain map not carried | {src_path.name} | {kind}: the JXL "
                       f"holds the standard (SDR) picture only; the lossless transcode "
                       f"(--force-transcode) keeps the whole JPEG. This JPEG is never "
                       f"deleted by a lossy conversion")


def _lossy_decode_keep_reason(src_path: Path, final_file: Path):
    """Why a JXL decoded by the CONVERT path must not be deleted, or None
    (T-B of the 2026-11-03 audit, the owner's option 1). A derivative never
    deletes its master; neither does a decode that is not a copy of it: a
    JPEG (lossy) or a PNG below 16 bits per sample — the decoder keeps its
    JXLs for an 8-bit TIFF of a deeper source the same way. A 16-bit PNG is
    a full-depth decode and may replace the JXL; the bit-exact JPEG recovery
    (jbrd) goes through the transcode path and is not judged here."""
    if src_path.suffix.lower() not in JXL_EXTS:
        return None
    ext = final_file.suffix.lower()
    if ext in JPEG_EXTS:
        return ("KEEP (a JPEG decoded from a JXL is lossy, not a copy of it — only "
                "the bit-exact recovery of a JPEG archive deletes its JXL)")
    if ext in PNG_EXTS:
        depth = _png_bit_depth(final_file)
        if depth != 16:
            return (f"KEEP (a {depth if depth else '?'}-bit PNG may be a degraded copy "
                    f"of the JXL — only a 16-bit PNG replaces it)")
        # #622: a 16-bit PNG still is not a copy of a float or deeper JXL
        # (an animated one is: the PNG keeps the frames).
        _red = _reduced_by_integer_decode(_jxlinfo_text(src_path), frames_kept=True)
        if _red:
            return (f"KEEP (the JXL holds {_red}: a 16-bit PNG is not a full copy "
                    f"of it)")
    return None


def _origin_marker_lines(src_path: Path) -> list:
    """The dc:Relation line giving a JPEG/PNG -> JXL pixel re-encode its
    jxlphoto-origin when the source has none and is clean (see ORIGIN_PREFIX).
    An inherited origin is already copied by _copy_metadata."""
    ext = src_path.suffix.lower()
    if ext in JPEG_EXTS:
        fresh = "jpeg"
    elif ext in PNG_EXTS:
        depth = _png_bit_depth(src_path)
        fresh = f"png{depth}" if depth in (8, 16) else "png"
    else:
        return []
    tokens, record = _read_origin_inputs(src_path)
    value, inherited = _origin_for_output(tokens, record, fresh)
    if value is None or inherited:
        return []
    return ["-XMP-dc:Relation+=" + ORIGIN_PREFIX + _argfile_safe(value)]


_resize_geometry = _common._resize_geometry


def _sharpen_args(*a, **k): return _common._sharpen_args(_G, *a, **k)


_resize_label = _common._resize_label


_derived_label = _common._derived_label


def _read_thumb_markers_batch(paths: list) -> set:
    """normcased paths of the JXLs among `paths` marked jxlphoto-thumb (the
    thumbnail page of a split TIFF) or that are a camera's unflagged 160 px
    thumbnail page (_is_camera_thumbnail_page), in as few exiftool calls as
    possible. A batch that cannot be read marks nothing: its files are
    converted as before."""
    found = set()
    if not paths:
        return found
    batch_lines = ["-j", "-s", "-s", "-XMP-dc:Relation", "-ImageWidth", "-ImageHeight",
                   "-charset", "FileName=UTF8", "-charset", "UTF8"]
    BATCH = 400
    _prog = _batch_read_state("Thumbnail markers", len(paths))
    for i in range(0, len(paths), BATCH):
        chunk = paths[i:i + BATCH]
        _batch_read_tick(_prog, i)
        argfile = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             dir=TEMP_DIR, encoding="utf-8",
                                             newline=chr(10)) as af:
                af.write(chr(10).join(batch_lines + [str(p) for p in chunk]))
                af.write(chr(10))
                argfile = af.name
            r = _run_captured([_get_exiftool_cmd(), "-@", argfile],
                              EXIFTOOL_BATCH_TIMEOUT, text=True,
                              stall=EXIFTOOL_BATCH_STALL)
            if not r.stdout:
                continue
            for entry in json.loads(r.stdout):
                src = entry.get("SourceFile")
                rel = entry.get("Relation")
                if src is None or rel is None:
                    continue
                values = rel if isinstance(rel, list) else [str(rel)]
                if (any(str(t).strip() == THUMB_FLAG for t in values)
                        or _is_camera_thumbnail_page(values, entry.get("ImageWidth"),
                                                     entry.get("ImageHeight"))):
                    found.add(os.path.normcase(str(Path(src))))
        except Exception as e:
            logger.debug(f"Thumbnail-page check: marker batch failed ({e})")
        finally:
            if argfile:
                try:
                    os.unlink(argfile)
                except OSError:
                    pass
    return found


_DERIVED_INPUT_LABELS: dict = {}


def _drop_derivative_pairs(pairs, mode):
    """The decode direction's pairs minus the JXL sources that carry a
    jxlphoto-derived marker — modes 6/7 without an explicit subfolder (the
    261103 review of delivery A, S2). A derivative next to its master shares
    the master's name and lands on the same JPEG/PNG. A source whose marker
    cannot be read is kept (the duplicate-output check still guards it).
    The labels are remembered for the run: auto mode plans twice."""
    if not (mode == 6 or (mode == 7 and not EXPORT_JPEG_SUBFOLDER)):
        return pairs
    srcs = [str(s) for s, _o in pairs if s.suffix.lower() in JXL_EXTS]
    todo = [p for p in srcs if p not in _DERIVED_INPUT_LABELS]
    if todo:
        _DERIVED_INPUT_LABELS.update(_read_derived_markers_batch(todo))
    kept = [(s, o) for s, o in pairs if not _DERIVED_INPUT_LABELS.get(str(s))]
    _n = len(pairs) - len(kept)
    if _n:
        logger.info(f"Ignored {_n} derivative JXL(s) below the export marker (they carry a jxlphoto-derived marker: copies made from a master, not masters) — to process them, run mode 7 with --export-subfolder set to their folder")
    return kept


def _skip_thumbnail_pages(pairs, dry_run=False):
    """The pairs minus those whose source is the THUMBNAIL page of a split
    TIFF (<name>_page1_thumbnail.jxl, marked jxlphoto-thumb): a resized,
    sharpened or colour-converted copy is made of the photo, not of the
    preview its TIFF carried (#555, the transcoder's side of the
    recompressor's #552). Logged once. A dry run reads no marker (it runs no
    subprocess), so its preview still lists them."""
    if dry_run:
        return pairs
    thumbs = _read_thumb_markers_batch([s for s, _o in pairs
                                        if s.suffix.lower() == ".jxl"])
    if not thumbs:
        return pairs
    kept = [(s, o) for s, o in pairs if os.path.normcase(str(s)) not in thumbs]
    logger.info(f"{len(pairs) - len(kept)} thumbnail page(s) of split TIFFs skipped: "
                f"a derivative is made of the photo, not of the preview its TIFF "
                f"carried.")
    return kept


def _is_derivative() -> bool:
    """True when this run shapes output pixels: resize and/or sharpen.

    Such an output is a DERIVATIVE: it never replaces or deletes its source,
    never proves the master is archived, and carries
    jxlphoto-derived:<recipe> instead of the jxlphoto-src pair.
    """
    return bool(RESIZE_MODE or SHARPEN != "none")


def _output_icc_label(output_icc):
    """The label of an --icc-profile target: sRGB, icc-<md5[:12]>, or None.

    Same spelling the recompressor's _resolve_output_icc produces, so a label
    written by one script is recognized by the other.
    """
    if not output_icc:
        return None
    if output_icc in ("sRGB", "AdobeRGB"):
        return output_icc
    try:
        data = Path(output_icc).read_bytes()
        return "icc-" + hashlib.md5(data).hexdigest()[:12]
    except OSError:
        return "icc-" + Path(output_icc).stem


def _current_derived_label(output_icc) -> str:
    """The recipe this run writes: _derived_label (the one the recompressor
    uses too) plus, for a JPEG, its quality (`/q70`, v2.25.0)."""
    label = _derived_label(_output_icc_label(output_icc), RESIZE_MODE,
                           RESIZE_VALUE, ALLOW_UPSCALE, SHARPEN)
    return label + (f"/q{LABEL_QUALITY}" if LABEL_QUALITY else "")


_LABEL_QUALITY_RE = re.compile(r"/q(\d+)$")


def _recipe_changed(stored: str, wanted: str) -> bool:
    """Does an existing derivative's recorded recipe ask for a new one?
    A different colour/size/sharpening always does; a different JPEG
    quality does too. A label without a quality (written before v2.25.0, or
    a PNG) is not compared on it: the quality it was made with is unknown,
    so an update never redoes every existing JPEG."""
    if stored == wanted:
        return False
    s_q, w_q = _LABEL_QUALITY_RE.search(stored), _LABEL_QUALITY_RE.search(wanted)
    s_base = stored[:s_q.start()] if s_q else stored
    w_base = wanted[:w_q.start()] if w_q else wanted
    if s_base != w_base:
        return True
    if s_q is None or w_q is None:
        return False
    return s_q.group(1) != w_q.group(1)


def _check_derivative_targets(pairs, output_icc, dry_run=False):
    """(kept_pairs, refused) for a resize/sharpened run.

    An existing output that is one of our derivatives with the CURRENT recipe
    is left to the skip logic; a derivative with a DIFFERENT recipe is
    re-derived; an existing file that is NOT a derivative (or whose markers
    cannot be read) is refused — a JPEG the user exported themselves must
    never be silently overwritten, and a derived output must never be used to
    prove the master is archived. Returns refused as [(src, out, why)] like
    _provenance_filter.
    """
    if not _is_derivative():
        return pairs, []
    existing = [(s, o) for s, o in pairs if o.exists()]
    if not existing:
        return pairs, []
    labels = _read_derived_markers_batch([o for _s, o in existing])
    wanted = _current_derived_label(output_icc)
    refused, refused_keys = [], set()
    for s, o in existing:
        lab = labels.get(str(o), False)
        if lab is False or lab is None:
            why = (f"an existing file at the destination is not a derivative of "
                   f"this run (wanted jxlphoto-derived:{wanted}) — refusing to "
                   f"overwrite what may be a user export")
            refused.append((s, o, why))
            refused_keys.add(os.path.normcase(str(o)))
            if dry_run:
                logger.info(f" DRY | would REFUSE | {s.name} | {why}")
            else:
                logger.error(f"REFUSED | {s.name} | {why}")
        elif _recipe_changed(lab, wanted):
            _FORCE_REDERIVE.add(os.path.normcase(str(o)))
            logger.info(f" derivative recipe changed ({lab} -> {wanted}): "
                        f"re-deriving | {s.name}")
    if refused_keys:
        pairs = [(s, o) for s, o in pairs
                 if os.path.normcase(str(o)) not in refused_keys]
    return pairs, refused


def _derivative_metadata_args(jxl_path: Path, label: str, size) -> list:
    """exiftool lines that turn the copied metadata into a derivative's.

    dc:Relation: drop jxlphoto-src/srcsum (trap A7: a resized JPEG must never
    prove the master is archived) and any old jxlphoto-derived, keep everything
    else, add the current recipe's label. When the output was resized, the
    EXIF/XMP pixel dimensions copied from the master are corrected to the
    output's own (the XMP pair only when the source carried it).

    The dc:Relation CLEAR is a separate invocation on purpose: with `=` and
    `+=` for the same list tag in ONE exiftool call, exiftool 13.59 ignores
    the clear when the target already carries a dc:Relation (verified
    2026-09-25) — the master's jxlphoto-src survived onto the derivative.
    """
    _creator, tokens = _read_creator_and_relation(jxl_path)
    # ICC_PROFILE_PREFIX too: a delivered JPEG/PNG has its profile in the
    # normal place (_copy_metadata strips the copied token the same way).
    # TIFFTAG_PREFIX: only a JXL -> TIFF decode reads those.
    drop = (SRC_PREFIX, SRCSUM_PREFIX, DERIVED_XMP_PREFIX, ICC_PROFILE_PREFIX,
            ORIENTATION_PREFIX, TIFFTAG_PREFIX)
    keep = [t for t in tokens if t and not t.startswith(drop)]
    lines = ["-XMP-dc:Relation+=" + _argfile_safe(t) for t in keep]
    lines.append("-XMP-dc:Relation+=" + DERIVED_XMP_PREFIX + label)
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


def _output_magick_args(jxl_path: Path, tmp_png: Path, tmp_dir: Path,
                        output_icc, bit_depth: int, quality: int,
                        is_png: bool, float_src=None, unrotate=None) -> tuple:
    """(magick args, size|None, grey, converted) for the derivative/ICC pass.

    Order validated in the plan: source profile -> target conversion ->
    Lanczos resize -> Lab sharpening -> output-profile re-assign (B2) ->
    depth/quality.

    float_src: (pfm_path, float_icc_path, orig_icc_path) when djxl decoded a
    lossy ICC-blob file to LINEAR sRGB (linear grey for a grey file, which
    goes back to its own grey profile). The magick INPUT is the float PFM
    (tmp_png only still provides size/grey detection), the source profile is
    djxl's own (assigned explicitly — the PFM carries none), and WITHOUT an
    output_icc the pixels are CONVERTED to the original ICC (re-assigning
    linear sRGB would leave the master's space).
    """
    grey = _png_is_grayscale(tmp_png)
    derivative = _is_derivative()
    args = []
    converted = bool(output_icc) and not grey
    src_profile = None
    if grey and float_src is not None:
        # Grey master with a table-curve grey profile: djxl returned LINEAR
        # grey. Grey is never colour-converted, so go back to the file's own
        # grey profile (the caller hands djxl's --orig_icc_out here).
        _pfm, float_icc, orig_target = float_src
        args += ["-profile", str(float_icc), "-intent", "Relative",
                 "-black-point-compensation", "-profile", str(orig_target)]
    elif not grey:
        if float_src is not None:
            _pfm, float_icc, orig_target = float_src
            args += ["-profile", str(float_icc)]
            src_profile = float_icc
            if output_icc:
                args += _magick_icc_args(output_icc, [])
            else:
                args += ["-intent", "Relative", "-black-point-compensation",
                         "-profile", str(orig_target)]
                src_profile = orig_target
        else:
            src_args, src_profile = _source_profile_args(jxl_path, tmp_png, tmp_dir)
            args += src_args
            if output_icc:
                args += _magick_icc_args(output_icc, [])
    size = None
    if derivative and RESIZE_MODE:
        w, h = _png_size(tmp_png)
        geom = _resize_geometry(w, h, RESIZE_MODE, RESIZE_VALUE, ALLOW_UPSCALE)
        if geom:
            args += ["-filter", "Lanczos", "-resize", f"{geom[0]}x{geom[1]}!"]
            size = geom
        else:
            logger.info(f"  Already smaller than the target — kept at "
                        f"{w}×{h} | {jxl_path.name}")
    if derivative:
        sharp = _sharpen_args(SHARPEN, SHARPEN_SIGMA, SHARPEN_GAIN,
                              SHARPEN_THRESHOLD, grey=grey)
        args += sharp
        if sharp and not grey:
            # B2: `-colorspace Lab ... -colorspace sRGB` drops the ICC profile;
            # re-assign the profile that describes the (converted) pixels.
            if output_icc:
                args += _magick_icc_args(output_icc, [])
            elif src_profile is not None:
                args += ["-profile", str(src_profile)]
    if unrotate:
        # JPEG_ORIENTATION = "original": back into the order the JPEG stored
        # the pixels in, after every colour and size step. A resized output's
        # width and height swap with it for the quarter turns.
        args += _UNROTATE_MAGICK[unrotate]
        if size and unrotate >= 5:
            size = (size[1], size[0])
    args += ["-depth", str(bit_depth)]
    if not is_png:
        args += ["-quality", str(quality)]
        if unrotate and not derivative and not output_icc:
            # This file took the ImageMagick route only to be turned back; djxl,
            # which would have written it, keeps 4:4:4 at every quality, while
            # ImageMagick drops to 4:2:0 below quality 90 (measured: 2
            # ssimulacra2 points lower).
            args += ["-sampling-factor", "4:4:4"]
    return args, size, grey, converted


def _icc_args_for(png_path: Path, output_icc: str, extra: list) -> list:
    """Colour args for one decoded intermediate, skipping the profile on grey.

    An RGB ICC on a single-channel image is malformed: PNG requires the iCCP
    profile's data colour space to match the colour type, so libpng warns
    ("RGB color space not permitted on grayscale PNG") on every read, and a
    1-component JPEG carrying an sRGB profile is wrong in the same way.
    ImageMagick attaches it without converting the image, so the caller has to
    decide.

    Nothing is converted to RGB to make the profile fit: a grey image has no
    gamut to map, and widening it would triple every film-scan IR page. The
    encoder learned this first — it does not apply an inherited RGB ICC to a
    grayscale page either.
    """
    if _png_is_grayscale(png_path):
        logger.info(f"  >Grayscale image: skipping the ICC conversion "
                    f"({png_path.name} has no colour to map, and an RGB profile "
                    f"on a single-channel file is invalid)")
        return list(extra)
    return _magick_icc_args(output_icc, extra)


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

    # Use should_process for consistent logic. An existing derivative whose
    # recorded recipe names another one is re-derived even without
    # --overwrite (the planner put it in _FORCE_REDERIVE).
    _forced = os.path.normcase(str(final_path)) in _FORCE_REDERIVE
    if not _forced and not should_process(jxl_path, final_path, smart, reconvert_val):
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
    # Never write under the final name without staging (see
    # encode_one_transcode): uuid temp beside the final + atomic os.replace
    # after the integrity check. ".tmp" suffix per the item-26 rule: no
    # orphan beside the final is adoptable as real input. Because djxl and
    # magick both infer the output format from the file EXTENSION, the ".tmp"
    # name cannot be handed to them directly: the codec branches below
    # translate it back with --output_format (djxl) / an explicit format
    # (magick - png|jpeg), and "direct" djxl-to-JPEG goes through the
    # --output_format=jpeg flag.
    _promote_local = write_path == final_path
    if _promote_local:
        write_path = final_path.parent / f"{uuid.uuid4().hex}_{final_path.name}.tmp"
    actual_out = write_path
    _pre_identity = _capture_output_identity(write_path, final_path)

    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)

        is_png = (fmt == "png")
        # NOTE: the JPEG+16-bit -> PNG switch happens in the CALLERS
        # (cmd_convert / _process_file_group) before output pairs are built,
        # so staging and final paths always agree. A runtime switch here would
        # make should_process consult the wrong extension (dead code, removed).

        # The beside-final temp ends in ".tmp" (item 26), which djxl/magick
        # cannot map to a format by extension. djxl gets --output_format
        # (_fmt_flag) on the direct branches; magick gets an explicit
        # "jpg:"/"png:" prefix on the output (it otherwise falls back to the
        # INPUT's format for an unknown extension, which would write PNG
        # bytes into a JPEG deliverable). With staging the extension is real
        # and the explicit spec is a harmless no-op.
        _fmt_flag = "--output_format=png" if is_png else "--output_format=jpeg"
        _magick_out = ("png:" if is_png else "jpg:") + _magick_out_path(actual_out)

        derivative = _is_derivative()
        if output_icc and not MAGICK_AVAILABLE:
            # Fail loudly: silently keeping the embedded ICC would deliver
            # files in the wrong color space while the log says "converting".
            # (Before output_dirty is set: a pre-existing output is NOT touched.)
            raise RuntimeError(
                "ICC conversion requested but ImageMagick (magick) is not in PATH. "
                "Install ImageMagick or drop --icc-profile/--to-srgb.")
        if derivative and not MAGICK_AVAILABLE:
            raise RuntimeError(
                "--resize-*/--sharpen need ImageMagick (magick) in PATH. "
                "Install ImageMagick or drop the resize/sharpening flags.")

        # JPEG_ORIENTATION = "original": a JXL whose photo was a rotated JPEG
        # gives the JPEG its stored pixel order and its EXIF Orientation back.
        _unrot = None
        if not is_png and JPEG_ORIENTATION == "original":
            _unrot = _stored_orientation(jxl_path)
            if _unrot and not MAGICK_AVAILABLE:
                if "no-magick-orientation" not in _JXLINFO_NOTES:
                    _JXLINFO_NOTES.add("no-magick-orientation")
                    logger.warning("ImageMagick (magick) is not in PATH: a JPEG made "
                                   "from a JXL of a rotated JPEG comes out upright, "
                                   "with Orientation 1, instead of in its original "
                                   "orientation")
                _unrot = None

        # From here on, a tool writes to actual_out; on failure the partial
        # output must be removed (see except below).
        output_dirty = True

        # Resize/sharpening (and every ICC conversion) needs pixels in an
        # intermediate: ONE magick branch for PNG and JPEG. The intermediate is
        # decoded at 16 bits whenever the pixels will be resized or sharpened —
        # the final --bit-depth is applied at the end of the chain.
        size = None

        def _via_magick():
            """The ImageMagick pipeline (colour conversion, derivative, and
            the lossy ICC-blob decode of #568); returns the derivative size."""
            size = None
            with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
                tmp_png = Path(tmp) / "tmp.png"
                _decode_bits = 16 if (derivative or _unrot) else bit_depth
                icc_args, out_icc, orig_icc = _djxl_icc_args(Path(tmp))
                r = _run_captured(["djxl", str(jxl_path), str(tmp_png), f"--bits_per_sample={_decode_bits}"] + icc_args, CODEC_TIMEOUT)
                if r.returncode != 0:
                    raise RuntimeError(f"djxl: {_stderr_tail(r.stderr)}")
                # A lossy JXL carrying a whole ICC blob decodes to LINEAR
                # sRGB, not to the file's own space: the intermediate must
                # be the float PFM with djxl's own profile assigned, never
                # the PNG labelled with the XMP ICC.
                float_src = None
                magick_in = tmp_png
                if _decoded_in_original_space(out_icc, orig_icc) is False:
                    if _png_has_alpha(tmp_png):
                        # The float PFM has no alpha, and alpha is not
                        # colour-managed: merging it back through the
                        # recipe is not implemented. Fail closed rather
                        # than silently dropping the channel.
                        raise RuntimeError(
                            "lossy ICC blob with an alpha channel: the "
                            "correct decode goes through a float PFM, which "
                            "has no alpha — refusing to convert")
                    pfm_path = Path(tmp) / "dec.pfm"
                    float_icc = Path(tmp) / "float_out.icc"
                    r = _run_captured(["djxl", str(jxl_path), str(pfm_path),
                                       f"--icc_out={float_icc}"],
                                      CODEC_TIMEOUT)
                    if r.returncode != 0:
                        raise RuntimeError(f"djxl: {_stderr_tail(r.stderr)}")
                    creator, _tokens = _read_creator_and_relation(jxl_path)
                    # Grey: djxl's own original profile, always single-
                    # channel (an XMP ICC could be RGB).
                    _xmp_icc = (None if _png_is_grayscale(tmp_png)
                                else _carried_icc(creator, _tokens))
                    if _xmp_icc:
                        orig_target = Path(tmp) / "orig_target.icc"
                        orig_target.write_bytes(_xmp_icc)
                    else:
                        orig_target = orig_icc
                    if not output_icc and _icc_a2b_only(orig_target.read_bytes()):
                        # A "keep" result must go back INTO the master's
                        # profile; one with A2B tables and no B2A (a scanner
                        # profile) cannot be a faithful target (R1, 27 dB).
                        raise RuntimeError(
                            "this lossy file stores an ICC blob whose profile has "
                            "A2B tables but no B2A (an input/scanner profile): "
                            "nothing can convert its pixels back INTO that "
                            "profile faithfully — convert to a working space "
                            "instead (--to-srgb / --icc-profile)")
                    float_src = (pfm_path, float_icc, orig_target)
                    magick_in = pfm_path
                    logger.info(f"  >ICC blob in a lossy file: converting from "
                                f"the float decode (djxl's linear output), not "
                                f"from the XMP-labelled PNG | {jxl_path.name}")
                magick_output, size, grey, _converted = _output_magick_args(
                    jxl_path, tmp_png, Path(tmp), output_icc, bit_depth,
                    quality, is_png, float_src=float_src, unrotate=_unrot)
                logger.debug(f"Using ICC/derivative pipeline: {magick_output[:3]}")
                r = _run_captured(["magick", str(magick_in)] + magick_output
                                  + (_MAGICK_KEEP_ICCP if is_png else [])    # #635
                                  + [_magick_out], CODEC_TIMEOUT)
                if r.returncode != 0:
                    raise RuntimeError(f"magick: {_stderr_tail(r.stderr)}")
                if not grey:
                    _verify_profile_in_output(actual_out, is_png=is_png)
            return size

        _djxl_wrote_it = False
        if (output_icc and MAGICK_AVAILABLE) or derivative or _unrot:
            size = _via_magick()
        else:
            _djxl_wrote_it = True
            # Direct djxl to PNG/JPG (explicit format: the temp may end in
            # .tmp). The same call reports which colour space djxl decoded to
            # and what the file's own is (#568).
            with tempfile.TemporaryDirectory(dir=TEMP_DIR) as _itmp:
                icc_args, out_icc, orig_icc = _djxl_icc_args(Path(_itmp))
                if is_png:
                    cmd = (["djxl", _fmt_flag] + icc_args
                           + [str(jxl_path), str(actual_out),
                              f"--bits_per_sample={bit_depth}"])
                else:
                    cmd = (["djxl", _fmt_flag, f"--jpeg_quality={quality}"] + icc_args
                           + [str(jxl_path), str(actual_out)])
                r = _run_captured(cmd, CODEC_TIMEOUT)
                if r.returncode != 0:
                    raise RuntimeError(f"djxl: {_stderr_tail(r.stderr)}")
                _space = _decoded_in_original_space(out_icc, orig_icc)
            if _space is False:
                # A lossy JXL carrying a whole ICC blob: djxl's direct output
                # is not in the file's own space, and labelling it with the
                # original profile would be wrong colour. Convert it back from
                # the float decode, like an --icc-profile run does.
                if not MAGICK_AVAILABLE:
                    raise RuntimeError(
                        "this lossy JXL stores an ICC blob: djxl's direct output is "
                        "not in the file's own colour space, and the conversion "
                        "needs ImageMagick (magick) in PATH")
                logger.info(f"  >ICC blob in a lossy file: converting through "
                            f"ImageMagick instead of djxl's direct output | "
                            f"{jxl_path.name}")
                size = _via_magick()
                _djxl_wrote_it = False
            elif _space is True:
                # The pixels are in the file's own space; the profile that
                # describes them is the one the master carries in XMP. A
                # master whose encode skipped the iCCP (cautious/heuristic/
                # skip) has its pixels tagged sRGB in the codestream, and the
                # output was labelled sRGB — wrong colour, and the token
                # holding the real profile is stripped below.
                _relabel_with_carried_icc(jxl_path, actual_out, is_png)
            else:
                logger.debug(f"djxl did not report the colour spaces — output "
                             f"kept as decoded | {jxl_path.name}")

        # Preserve EXIF/XMP/IPTC metadata that djxl/ImageMagick may drop. A
        # derivative gets the provenance markers REMOVED in the same breath
        # (trap A7: a 2048 px JPEG must never prove the master is archived, or
        # a later --delete-source --delete-skipped run would unlink the master).
        if is_png and _djxl_wrote_it:
            # #630: djxl's own copy of the EXIF/XMP, in non-standard chunks,
            # makes exiftool fail on a PNG with EXIF and no XMP.
            _drop_djxl_png_profiles(actual_out)
        try:
            _copy_metadata(jxl_path, actual_out)
        except RuntimeError as _e_meta:
            # #631: a malformed EXIF reaches a JPEG as a block (djxl and
            # ImageMagick copy it), and exiftool cannot rewrite it. Without
            # that copy the metadata is written fresh from the JXL; a second
            # failure is the file's error.
            if is_png or not _drop_jpeg_codec_metadata(actual_out):
                raise
            logger.info(f"  >metadata copied again without the EXIF/XMP the codec "
                        f"wrote into the JPEG | {jxl_path.name} | first try: {_e_meta}")
            _copy_metadata(jxl_path, actual_out)
        # The pixels were put back in their stored order: so is the tag
        # (_copy_metadata left it at 1, or at the token-less copy's 1).
        _orient = ([f"-IFD0:Orientation#={_unrot}", "-XMP-tiff:Orientation="]
                   if _unrot else [])
        if output_icc:
            # #637: the copied InteropIndex names the SOURCE's DCF colour
            # space; a converted output is in another one.
            _orient += ["-InteropIFD:InteropIndex=", "-InteropIFD:InteropVersion="]
        if derivative:
            _dlabel = _current_derived_label(output_icc)
            # Separate CLEAR invocation (see _derivative_metadata_args): the
            # copied jxlphoto-src must not survive `=` + `+=` in one call.
            rc = _run_exiftool_argfile(
                ["-overwrite_original", "-XMP-dc:Relation=", str(actual_out)],
                timeout=EXIFTOOL_TIMEOUT)
            if rc.returncode != 0:
                raise RuntimeError(f"derivative metadata clear: {(rc.stderr or '')[:200]}")
            r2 = _run_exiftool_argfile(
                ["-overwrite_original"]
                + _derivative_metadata_args(jxl_path, _dlabel, size)
                + _orient
                + _history_args("derived", jxl_path, actual_out)
                + [str(actual_out)], timeout=EXIFTOOL_TIMEOUT)
            if r2.returncode != 0:
                raise RuntimeError(f"derivative metadata write: {(r2.stderr or '')[:200]}")
        else:
            # An ERROR for the file when it fails (#571), like the copy above.
            r_prov = _run_exiftool_argfile(
                ["-overwrite_original"] + _inherited_marker_removal_lines(actual_out)
                + _provenance_marker_args(jxl_path)
                + _orient
                + _history_args("converted", jxl_path, actual_out)
                + [str(actual_out)], timeout=EXIFTOOL_TIMEOUT)
            if r_prov.returncode != 0:
                raise RuntimeError(
                    f"provenance/history write failed (exiftool rc={r_prov.returncode}): "
                    f"{(r_prov.stderr or r_prov.stdout or '').strip()[:200]}")

        # Validate EVERY successful output (rc=0 does not guarantee a
        # well-formed file).
        if not _verify_file_integrity(actual_out):
            raise RuntimeError("decode returned 0 but the output failed the integrity check")

        if _promote_local:
            _swap_into_place(actual_out, final_path)

        n, total = next_count()
        if derivative:
            label = f"DERIVE ({_current_derived_label(output_icc)})"
        else:
            label = "RECONVERT" if overwritten else "OK"
        logger.info(f"[{n}/{total}] {label} | {jxl_path.name} -> {final_path.name}")
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

@_holds_staging
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
    refused_moves = {}      # str(src) -> why (#615)
    failed_moves = set()    # str(src) (#624)

    def _move_dest_from_staging(dest_tasks, status_map):
        """Bulk-move one destination folder's outputs out of staging."""
        moved = 0
        for src, write_out, final_out in dest_tasks:
            status = status_map.get(str(src), "error")
            if status in ("ok", "reconvert", "overwrite"):
                if not write_out.exists():
                    # A "successful" worker whose staged output is gone must be
                    # reported, not dropped — same KEEP (staging file missing)
                    # the transcode mover logs and counts.
                    _delete_stats["kept"] += 1
                    logger.warning(f"  KEEP (staging file missing) | {write_out.name}")
                    continue
                # A locked/readonly destination must not abort the whole batch:
                # the file stays in staging and is logged for manual recovery.
                _why = _output_changed_since_planned(final_out)
                if _why:
                    refused_moves[str(src)] = _why
                    logger.error(f"  REFUSED, kept in staging | {write_out.name} -> "
                                 f"{final_out} | {_why}")
                    continue
                if _promote_from_staging(write_out, final_out):
                    moved += 1
                    moved_finals.add(os.path.normcase(str(final_out)))
                    if direction == "to_jxl":
                        _file_checksums(final_out, result_by_src.get(str(src)))
                else:
                    failed_moves.add(str(src))
            elif write_out.exists():
                # Failed conversion: do not promote a partial/corrupt file to the
                # final destination. Leave it in staging so the user can inspect.
                logger.warning(f"Staging: not promoting {write_out.name} (status={status})")
        if moved:
            logger.info(f" -> Moved {moved} file(s) from staging to {dest_tasks[0][2].parent}")

    # Filed right after each move (#556), as in process_group_transcode.
    filed = set()
    result_by_src = {}

    def _file_checksums(final_out, r):
        if r is None or r[1] not in ("ok", "reconvert") or len(r) < 4 or not r[3]:
            return
        if _file_checksum_lines(final_out, r[3], r[4] if len(r) > 4 else None):
            filed.add(os.path.normcase(str(final_out)))

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
    for _s, _w, _f in tasks:
        _record_source_identity(_s)      # X2: before anything reads it
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
            result_by_src[result[0]] = result

            # Flush this destination folder once every one of its files is done.
            # Runs on the main thread (inside the as_completed loop), so no two
            # moves ever race for the same file.
            dest = task[2].parent
            pending_by_dest[dest] -= 1
            if use_staging and pending_by_dest[dest] == 0:
                _move_dest_from_staging(tasks_by_dest[dest], status_map)

    # Distribute each checksum to the folder its own output landed in, keyed
    # by the TASK (mirror of process_group_transcode; only the to_jxl encode
    # direction creates checksums). Without this, a --force-convert -d 0 run
    # with --staging left the jbrd output's provenance dead in the staging db.
    if use_staging and direction == "to_jxl":
        _stranded = 0
        for _r in results:
            if _r[1] not in ("ok", "reconvert") or len(_r) < 4 or not _r[3]:
                continue
            _final = Path(_r[2])
            # Only for outputs that actually reached their destination: a
            # checksum must never claim coverage of a file still in staging.
            if os.path.normcase(str(_final)) not in moved_finals:
                _stranded += 1
                continue
            if os.path.normcase(str(_final)) in filed:
                continue                        # filed at its move (#556)
            _file_checksum_lines(_final, _r[3], _r[4] if len(_r) > 4 else None)  # a retry
        staging_db = staging_dir / CHECKSUMS_FILENAME
        if staging_db.exists():
            if _stranded:
                logger.warning(
                    f"  Keeping {staging_db}: {_stranded} output(s) are still in "
                    f"staging and their checksums live only there. Move the file "
                    f"and its line to the destination folder before the next run: "
                    f"a new run starts with an empty staging checksums.md5.")
            else:
                try:
                    staging_db.unlink()
                except OSError:
                    pass

    if refused_moves:
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
    # moved_finals lets the mode-8 delete gate distinguish "this run's output
    # arrived at the final path" from a stale pre-existing file whose
    # overwrite FAILED (the gate's integrity check would otherwise certify
    # the old file and delete the source anyway).
    return results, moved_finals

def cmd_convert(args, from_jxl: bool = True):
    """Returns (errors, cancelled)."""
    global _counter, TEMP2_DIR, DELETE_SOURCE
    _counter = {"done": 0, "total": 0}
    _planned_outputs.clear()        # run-scoped (#615)
    _FORCE_REDERIVE.clear()

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
    # A dry run runs no subprocess (the contract tests pin): the floor needs
    # `cjxl --version`, so it is resolved only when the run really encodes.
    # Real runs warn exactly as before.
    if not args.dry_run:
        _warn_distance_clamp(args.distance, _min_effective_distance("cjxl"))

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
    if direction == "from_jxl" and _is_derivative() and not MAGICK_AVAILABLE:
        logger.error("--resize-*/--sharpen require ImageMagick (magick) in PATH.")
        sys.exit(1)
    if direction == "to_jxl" and args.icc_profile:
        logger.warning("--icc-profile/--to-srgb is ignored for JPEG/PNG -> JXL encodes "
                       "(no ICC-conversion step on that pipeline).")
    if direction == "to_jxl" and _is_derivative():
        logger.warning("--resize-*/--sharpen only apply when decoding JXL "
                       "(no pixel-shaping step on the JPEG/PNG -> JXL pipeline) — "
                       "ignored for this run.")

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
    if not OVERWRITE_GUARD:
        logger.warning("Overwrite guard OFF: an existing output made from another "
                       "source path is overwritten (and logged) instead of refused.")
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
                                         resolve_root, decode=False,
                                         single_file=args.input.is_file())
        else:
            # Default to jpg if format is somehow None, else use specified
            fmt = args.format if args.format else "jpeg"
            ext = "jpg" if fmt == "jpeg" else "png"
            out = resolve_output_convert(f, args.mode, args.output_name,
                                         _eff_suffix, ext,
                                         args.rename_from, args.rename_to,
                                         resolve_root, decode=True,
                                         single_file=args.input.is_file())
        if out is None:
            continue  # Skip files outside _EXPORT for modes 6/7
        pairs.append((f, out))

    # A resized, sharpened or colour-converted copy is made of the photo, not
    # of a split TIFF's thumbnail page (#555).
    if direction == "from_jxl" and (_is_derivative() or args.icc_profile):
        pairs = _skip_thumbnail_pages(pairs, dry_run=args.dry_run)
    if direction == "from_jxl" and not args.input.is_file():
        pairs = _drop_derivative_pairs(pairs, args.mode)

    # Progress total must reflect modes 6/7 filtering, not the raw scan
    _counter["total"] = len(pairs)
    if len(pairs) != len(files):
        logger.info(f"Planned: {len(pairs)} (filtered by mode)")

    _abort_on_duplicate_outputs(pairs)
    if not args.dry_run:
        _sweep_orphan_temps(dst for _, dst in pairs)
        _record_planned_outputs(pairs)
    pairs, _refused = _provenance_filter(
        pairs, args.mode,
        output_arg=args.output, source_root=_prov_src_root(args),
        would_write=lambda s, o: (os.path.normcase(str(o)) in _FORCE_REDERIVE
                                  or should_process(s, o, smart_mode, reconvert_explicit)),
        dry_run=args.dry_run, explicit_overwrite=reconvert_explicit)
    if _is_derivative():
        # A resize/sharpened output is a derivative: never overwrite a file
        # that is not one of ours, re-derive when the recipe changed.
        pairs, _der_refused = _check_derivative_targets(
            pairs, args.icc_profile, dry_run=args.dry_run)
        if _der_refused:
            _refused = _refused + _der_refused
    if _refused:
        _counter["total"] = len(pairs)

    if args.dry_run:
        # Same as cmd_transcode: an existing output is a SKIP in the real run
        # (should_process), not a conversion — except a derivative whose
        # recorded recipe changed, which the planner forces.
        _dry_pairs = [(f, out, (os.path.normcase(str(out)) in _FORCE_REDERIVE
                                or should_process(f, out, smart_mode, reconvert_explicit)))
                      for f, out in pairs]
        _n_skip = sum(1 for _f, _o, _go in _dry_pairs if not _go)
        _dlabel = _current_derived_label(args.icc_profile) if _is_derivative() else None
        for f, out, _go in _dry_pairs:
            if not _go:
                _lbl = "SKIP (up to date)" if smart_mode else "SKIP (exists)"
                logger.info(f" DRY | {_lbl} | {f.name}")
            else:
                _tag = f" [DERIVE: {_dlabel}]" if _dlabel else ""
                logger.info(f" DRY | {f.name} -> {out}{_tag}")
        logger.info(f"Dry run: {len(pairs) - _n_skip} files would be converted"
                    + (f"; {_n_skip} would be skipped (output already exists)."
                       if _n_skip else "."))
        # Same bug #267 as cmd_transcode: the ARMED preview only existed in
        # _process_file_group (cmd_auto), so a delete-armed convert dry run
        # never mentioned that sources would be destroyed.
        if DELETE_SOURCE:
            logger.warning(
                f"Dry run: --delete-source is ARMED. Up to {len(pairs) - _n_skip} "
                f"source file(s) would be DELETED, each only after its output is "
                f"written and passes the integrity check.")
        # Same rule as cmd_transcode: without this the wrapper reads the
        # untouched default and reports the simulation as a real run. The
        # refusals are failures the real run would charge (item 25 — the
        # dry run said errors=0 right below a "REFUSING N"), so they are
        # counted here too.
        if _refused:
            logger.warning(f"Dry run: {len(_refused)} pair(s) would be REFUSED by "
                           f"the provenance check (see above); the real run fails "
                           f"those files.")
        record_summary(ok=len(pairs) - _n_skip, overwritten=0, skipped=_n_skip,
                       errors=len(_refused),
                       log_file=log_file, dry_run=True)
        return (0, False)

    # Create the mode-2 output dir only for real runs (dry-run must not write)
    if args.mode == 2 and not args.input.is_file():
        output_root.mkdir(parents=True, exist_ok=True)

    groups = {}
    for f, out in pairs:
        groups.setdefault(out.parent, []).append((f, out))

    logger.info(f"Output groups: {len(groups)}")

    # Safety confirmation for DELETE_SOURCE, in every mode, but only when the
    # plan would even attempt a deletion (see _plan_would_delete_source)
    # Determine if operation is lossy based on direction and settings
    if DELETE_SOURCE and _plan_would_delete_source(pairs, smart_mode, reconvert_explicit):
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
    _source_identity.clear()
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
        _skip_marks = _lossy_skip_marks(results, src_map)
        for result in results:
            status = result[1]
            # LOSSY direction: see the note in _process_file_group — an
            # already-archived source needs its output's provenance marker.
            was_skipped = status == "skipped"
            if (status not in ("ok", "reconvert")
                    and not (DELETE_SKIPPED and was_skipped)):
                continue
            src_path, final_file = src_map.get(result[0], (None, None))
            if src_path is None:
                continue
            if not src_path.exists():
                logger.warning(f" {_SOURCE_GONE} | {src_path.name}")      # #619
                continue
            # Fail closed on a failed staging move: the file at the final
            # path would be a stale pre-existing one, not this run's output.
            if (TEMP2_DIR is not None and not was_skipped
                    and os.path.normcase(str(final_file)) not in moved_finals):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (output never left staging) | {src_path.name}")
                continue
            _why_keep = (_lossy_decode_keep_reason(src_path, final_file)
                         or _gain_map_keep_reason(src_path, final_file))
            if _why_keep:
                _delete_stats["kept"] += 1
                logger.warning(f" {_why_keep} | {src_path.name}")
                continue
            # THE real recovery gate, ported from the transcode path (process_
            # group_transcode): structural integrity is not enough when the
            # JXL carries a jbrd box. --force-convert --distance 0 on a JPEG
            # yields EXACTLY that (cjxl keeps jbrd at d=0), and a jbrd JXL can
            # pass every structural check while still failing
            # djxl --reconstruct_jpeg — e.g. v2.0.0-v2.0.3 wrote XMP markers
            # into jbrd containers, and exiftool's -tagsfromfile re-serialized
            # the XMP of any source that already had some. The source may only
            # be deleted when the reconstruction ACTUALLY reproduces its
            # bytes. With djxl < 0.12 the test cannot run, and an unverifiable
            # output must block deletion (fail closed).
            if (final_file.suffix.lower() == '.jxl'
                    and src_path.suffix.lower() in JPEG_EXTS
                    and has_jbrd_box(final_file)):
                if _tool_at_least("djxl", 0, 12):
                    if not _jxl_reconstructs_to(final_file, src_path):
                        _delete_stats["kept"] += 1
                        logger.warning(
                            f" KEEP (djxl --reconstruct_jpeg does not reproduce "
                            f"this JPEG; run --repair-jbrd to fix markers written "
                            f"by v2.0.0+) | {src_path.name}")
                        continue
                else:
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (djxl<0.12: bit-exact recovery cannot be verified) | {src_path.name}")
                    continue
            if final_file is None or not _verify_file_integrity(final_file):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (output failed integrity check) | {src_path.name}")
                continue
            if not _flush_to_disk(final_file):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (output could not be flushed to disk) | {src_path.name}")
                continue
            if was_skipped and _lossy_skip_unproven(src_path, final_file, _skip_marks):
                _delete_stats["kept"] += 1
                logger.warning(f" {_LOSSY_SKIP_KEEP} | {src_path.name}")
                continue
            if _source_changed_since_read(src_path):
                _delete_stats["kept"] += 1
                logger.warning(f" KEEP (source changed since this run read it (re-exported or rewritten during the run?) — its current version was never archived) | {src_path.name}")
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

    # Indexed, never unpacked at a fixed arity: encode_to_jxl's ok-result also
    # carries the source md5 and the JXL self-hash (round 43, #444) — a
    # 4-name unpack crashed every JPEG/PNG -> JXL --force-convert run right
    # after its last file converted, before the summary and the delete gate.
    for result in results:
        src, status, detail = result[0], result[1], result[2]
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
    _planned_outputs.clear()        # run-scoped (#615)
    _FORCE_REDERIVE.clear()

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
    # A dry run runs no subprocess (see cmd_convert): the clamp floor is
    # probed only on a real run.
    if not args.dry_run:
        _warn_distance_clamp(args.distance, _min_effective_distance("cjxl"))

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

    # #542: a DELETING auto run acts in one direction. Auto mode converts
    # both ways in one folder; armed with --delete-source it decoded a lossy
    # 16-bit master (a TIFF-encoder JXL, no jbrd) into an 8-bit JPEG and
    # deleted the master, and the next run of the same command turned the
    # JXLs it had just archived back into JPEGs. A deleting run decodes JXLs
    # only when that direction was asked for: --from-jxl (what the wrapper's
    # JXL -> JPEG workflow passes) or --decode (which, on a folder, never
    # reaches this function: it is the lossless jbrd recovery).
    if (DELETE_SOURCE and jxl_files
            and not (args.decode or getattr(args, "from_jxl", False))):
        logger.warning(f"--delete-source: {len(jxl_files)} JXL file(s) left untouched. "
                       f"A deleting run only decodes JXLs when asked to: "
                       f"--from-jxl (every JXL) or --decode (lossless recovery).")
        jxl_files = []

    if not jpeg_files and not png_files and not jxl_files:
        logger.warning("No JPEG, PNG or JXL files found.")
        # See cmd_transcode: an empty run still owes the wrapper a summary.
        record_summary(ok=0, overwritten=0, skipped=0, errors=0,
                       log_file=log_file, dry_run=args.dry_run)
        return (0, False)

    if _is_derivative() and (jpeg_files or png_files):
        # Resize/sharpening shape decoded pixels; the encode direction has no
        # such step (cjxl eats the source pixels). Say so rather than ignore.
        logger.warning("--resize-*/--sharpen only apply when decoding JXL: the "
                       "JPEG/PNG -> JXL groups in this run ignore them.")
    if args.icc_profile and (jpeg_files or png_files):
        # Mirror of cmd_convert's to_jxl warning: encode_to_jxl has no
        # colour-conversion step, so --icc-profile/--to-srgb silently did
        # nothing for these groups while decode/lossy groups honor it.
        logger.warning("--icc-profile/--to-srgb is ignored for JPEG/PNG -> JXL encodes "
                       "(no ICC-conversion step on that pipeline).")

    # Separate JXL files by jbrd presence
    jxl_transcode_files = []  # Have jbrd - can decode losslessly to JPEG
    jxl_convert_files = []    # No jbrd - must do lossy convert

    for f in jxl_files:
        if has_jbrd_box(f):
            jxl_transcode_files.append(f)
        else:
            jxl_convert_files.append(f)

    if _is_derivative() and jxl_transcode_files:
        # Resize/sharpening needs PIXELS: the bit-exact reconstruction cannot
        # shape them, so every jbrd file is decoded and re-encoded instead.
        logger.info(f"{len(jxl_transcode_files)} JXL(s) with a JPEG reconstruction are "
                    f"decoded and re-encoded instead (resize/sharpen needs pixels)")
        jxl_convert_files.extend(jxl_transcode_files)
        jxl_transcode_files = []

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
    if not OVERWRITE_GUARD:
        logger.warning("Overwrite guard OFF: an existing output made from another "
                       "source path is overwritten (and logged) instead of refused.")

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
    if not args.dry_run:
        _sweep_orphan_temps(dst for _, dst in all_pairs)

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
    if (DELETE_SOURCE and not args.dry_run and DELETE_CONFIRM
            and _plan_would_delete_source(
                [_p for _lst in planned.values() for _p in _lst],
                smart_mode, reconvert_explicit)):
        # Lossiness from the PLANNED pairs (post mode-6/7 filter), not the raw
        # lists — otherwise a fully filtered-out group still asks for the token.
        # PNG -> JXL is lossy only at distance > 0 (distance 0 is lossless
        # modular) — cmd_convert's rule; charging the strict HHMM token for a
        # lossless plan trained users to hand it out for nothing. But d=0 is
        # still LOSSLESS, so a PNG-only plan at d=0 must ask the WEAK
        # confirmation (confirm_deletion_jpeg) like cmd_convert does: with
        # both flags below False the folder was emptied with no confirmation
        # at all (2026-09-21 audit, item 6).
        has_lossy = bool(planned["JXL-lossy"]) or (bool(planned["PNG"]) and args.distance > 0)
        has_lossless = (bool(planned["JPEG"]) or bool(planned["JXL-jbrd"])
                        or (bool(planned["PNG"]) and args.distance <= 0))
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
    _source_identity.clear()
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
        # A dry run's tallies now model the real run: planned conversions in
        # `ok`, would-be-skips in `skipped` (the per-group tally above), with
        # provenance refusals charged as errors. This used to be
        # `len(all_pairs)` on a dry run — the pre-provenance-filter list — so
        # a refused pair was counted as a conversion (bug #15). `totals["err"]`
        # carries the refusals, so a simulated REFUSING N is never a clean run.
        ok=totals["ok"],
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
            out = resolve_output_transcode(f, args.mode, output_root, decode=not is_jpeg_input,
                                           single_file=args.input.is_file())
        else:
            out = resolve_output_convert(
                f, args.mode, args.output_name,
                args.output_suffix if args.output_suffix is not None else (CONVERT_OUTPUT_SUFFIX or ""),
                out_ext, args.rename_from, args.rename_to,
                resolve_root, decode=(direction == "from_jxl"),
                single_file=args.input.is_file()
            )
        if out:
            pairs.append((f, out))

    if direction == "from_jxl" and not args.input.is_file():
        pairs = _drop_derivative_pairs(pairs, args.mode)

    if collect_only is not None:
        # Pre-pass: only collect pairs for cross-group duplicate detection
        collect_only.extend(pairs)
        return {"ok": 0, "err": 0, "skipped": 0}

    # A resized, sharpened or colour-converted copy is made of the photo, not
    # of a split TIFF's thumbnail page (#555); the discount below counts them.
    if (not use_transcode and direction == "from_jxl"
            and (_is_derivative() or args.icc_profile)):
        pairs = _skip_thumbnail_pages(pairs, dry_run=args.dry_run)

    # Discount files filtered out by modes 6/7 from the progress total
    _counter["total"] = max(0, _counter.get("total", 0) - (len(files) - len(pairs)))

    _abort_on_duplicate_outputs(pairs)
    if not args.dry_run:
        _sweep_orphan_temps(dst for _, dst in pairs)
        _record_planned_outputs(pairs)
    pairs, _refused = _provenance_filter(
        pairs, args.mode,
        decode_lossless=(use_transcode and all(
            s.suffix.lower() == '.jxl' for s, _o in pairs)),
        output_arg=args.output, source_root=_prov_src_root(args),
        would_write=lambda s, o: should_process(s, o, args.sync, args.overwrite),
        dry_run=args.dry_run, explicit_overwrite=args.overwrite)
    if _is_derivative():
        # A resize/sharpened output is a derivative: never overwrite a file
        # that is not one of ours, re-derive when the recipe changed.
        pairs, _der_refused = _check_derivative_targets(
            pairs, args.icc_profile, dry_run=args.dry_run)
        if _der_refused:
            _refused = _refused + _der_refused
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
        # An existing output means the real run reports SKIP, not a conversion
        # (should_process — the same predicate the workers use), and the
        # provenance REFUSALS were already dropped from `pairs`. The tally
        # therefore carries the real plan, not `len(all_pairs)` (which counted
        # refused pairs as conversions — bug #15). cmd_transcode and
        # cmd_convert report their dry runs the same way.
        _dry_pairs = [(f, out, (os.path.normcase(str(out)) in _FORCE_REDERIVE
                                or should_process(f, out, args.sync, args.overwrite)))
                      for f, out in pairs]
        _n_skip = sum(1 for _f, _o, _go in _dry_pairs if not _go)
        _dlabel = _current_derived_label(args.icc_profile) if _is_derivative() else None
        for f, out, _go in _dry_pairs:
            if not _go:
                _lbl = "SKIP (up to date)" if args.sync else "SKIP (exists)"
                logger.info(f" DRY | {_lbl} | {f.name}")
            else:
                _tag = f" [DERIVE: {_dlabel}]" if _dlabel else ""
                logger.info(f" DRY | {f.name} -> {out}{_tag}")
        logger.info(f"Dry run: {len(pairs) - _n_skip} file(s) would be processed"
                    + (f"; {_n_skip} would be skipped (output already exists)."
                       if _n_skip else "."))
        if DELETE_SOURCE:
            logger.warning(
                f"Dry run: --delete-source is ARMED. Up to {len(pairs) - _n_skip} "
                f"source file(s) in this group would be DELETED.")
        # Zeros for the CONVERSION were wrong (a dry run's topline must model
        # the real run), but the provenance REFUSALS are separate: the real run
        # charges them as errors, so the simulation reports the same tally
        # instead of errors=0 right below a "REFUSING N" (item 25).
        # _provenance_filter has already logged each refusal, so they are
        # not invisible.
        _dry_tally = dict(_refused_tally)
        _dry_tally["ok"] = len(pairs) - _n_skip
        _dry_tally["skipped"] = _n_skip
        return _dry_tally

    if not pairs:
        # Everything in this group was refused. Returning zeros here was the
        # other half of the same bug.
        return _refused_tally

    # The transcode resolver takes no rename parameters (item 24): in auto
    # mode the convert groups DO apply --rename-from/--rename-to, so the
    # warning is scoped to the groups that ignore them — not shouted for the
    # whole run.
    if use_transcode and (getattr(args, "rename_from", "")
                          or getattr(args, "rename_to", "")):
        logger.warning("--rename-from/--rename-to are ignored for the JPEG->JXL "
                       "transcode paths in this group (only the lossy convert "
                       "path applies them)")

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
            _skip_marks = _lossy_skip_marks(results, src_map)
            for result in results:
                status = result[1]
                # LOSSY direction: nothing can be re-derived, but every lossy
                # output this script writes carries its source's provenance
                # marker — an already-archived source is deleted only when
                # that marker MATCHES (plus the structural check).
                was_skipped = status == "skipped"
                if (status not in ("ok", "reconvert")
                        and not (DELETE_SKIPPED and was_skipped)):
                    continue
                src_path, final_file = src_map.get(result[0], (None, None))
                if src_path is None:
                    continue
                if not src_path.exists():
                    logger.warning(f" {_SOURCE_GONE} | {src_path.name}")  # #619
                    continue
                # Fail closed on a failed staging move: the file at the
                # final path would be a stale pre-existing one.
                if (TEMP2_DIR is not None and not was_skipped
                        and os.path.normcase(str(final_file)) not in moved_finals):
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (output never left staging) | {src_path.name}")
                    continue
                _why_keep = (_lossy_decode_keep_reason(src_path, final_file)
                             or _gain_map_keep_reason(src_path, final_file))
                if _why_keep:
                    _delete_stats["kept"] += 1
                    logger.warning(f" {_why_keep} | {src_path.name}")
                    continue
                # THE real recovery gate, ported from the transcode path (same
                # as cmd_convert's gate): a jbrd-carrying JXL output (PNG->JXL
                # never has one, but a JPEG routed through a lossy convert at
                # d=0 does) must PROVE it still reconstructs the original JPEG
                # bit-exactly before the unlink. djxl < 0.12 cannot run the
                # test, so it fails closed.
                if (final_file.suffix.lower() == '.jxl'
                        and src_path.suffix.lower() in JPEG_EXTS
                        and has_jbrd_box(final_file)):
                    if _tool_at_least("djxl", 0, 12):
                        if not _jxl_reconstructs_to(final_file, src_path):
                            _delete_stats["kept"] += 1
                            logger.warning(
                                f" KEEP (djxl --reconstruct_jpeg does not reproduce "
                                f"this JPEG; run --repair-jbrd to fix markers written "
                                f"by v2.0.0+) | {src_path.name}")
                            continue
                    else:
                        _delete_stats["kept"] += 1
                        logger.warning(f" KEEP (djxl<0.12: bit-exact recovery cannot be verified) | {src_path.name}")
                        continue
                if final_file is None or not _verify_file_integrity(final_file):
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (output failed integrity check) | {src_path.name}")
                    continue
                if not _flush_to_disk(final_file):
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (output could not be flushed to disk) | {src_path.name}")
                    continue
                if was_skipped and _lossy_skip_unproven(src_path, final_file, _skip_marks):
                    _delete_stats["kept"] += 1
                    logger.warning(f" {_LOSSY_SKIP_KEEP} | {src_path.name}")
                    continue
                if _source_changed_since_read(src_path):
                    _delete_stats["kept"] += 1
                    logger.warning(f" KEEP (source changed since this run read it (re-exported or rewritten during the run?) — its current version was never archived) | {src_path.name}")
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

    parser.add_argument("--version", action="version",
                        version=f"{TOOLKIT_NAME} {TOOLKIT_VERSION}")

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
                             "A file path, or a built-in name: sRGB, AdobeRGB "
                             "(Adobe RGB (1998)-compatible)")
    parser.add_argument("--to-srgb", action="store_true",
                        help="Shortcut for --icc-profile sRGB")

    # Output shaping (JXL -> JPEG/PNG only; ignored with a warning when encoding)
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

    # Transcode specific
    parser.add_argument("--decode", action="store_true", help="Force decode direction")
    parser.add_argument("--no-md5", action="store_true", help="Skip MD5 storage")
    parser.add_argument("--no-verify", action="store_true", help="Skip MD5 verify on decode")
    parser.add_argument("--delete-source", action="store_true",
                        help="Delete source after a verified conversion, in ANY mode. "
                             "IRREVERSIBLE")
    parser.add_argument("--provenance", type=str, default=None,
                        choices=["path", "content"],
                        help="How an EXISTING output is matched to the source about "
                             "to overwrite it. Every --sync/--overwrite run refuses to "
                             "overwrite an output that is provably another source's "
                             "(marker; for lossless JPEG archives checksums.md5, or "
                             "djxl --reconstruct_jpeg when there is no line); "
                             "with --delete-source in modes 2/4/5/6/7 every existing "
                             "output must also prove it came from this source. "
                             "path (default, free) compares the recorded LOCATION; "
                             "content also accepts matching source bytes, so it "
                             "survives MOVED folders. The lossless JXL->JPEG path "
                             "uses checksums.md5 instead, since its output must stay "
                             "byte-identical and cannot carry a marker.")
    parser.add_argument("--no-overwrite-guard", action="store_true",
                        help="Allow overwriting an existing output whose provenance "
                             "marker names a DIFFERENT source (a renamed or moved "
                             "folder, or another photo with the same name). Each such "
                             "overwrite is logged. For libraries whose sources are "
                             "never deleted; refused together with --delete-source. "
                             "Same as OVERWRITE_GUARD = False.")
    parser.add_argument("--delete-skipped", action="store_true",
                        help="[with --delete-source] Also delete sources whose output "
                             "ALREADY EXISTS (reported as SKIP), so an archive interrupted "
                             "between the conversion and the unlink can be finished. "
                             "Lossless JPEG<->JXL: provenance is PROVEN against "
                             "checksums.md5. LOSSY directions: the existing output must "
                             "carry the provenance marker of THIS source (every lossy "
                             "output this script writes has one) and pass the "
                             "structural check")
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
    parser.add_argument("--export-jpeg-folder", type=str, default=None,
                        help="[Modes 6/7, JXL -> JPEG/PNG] Output folder created under "
                             "the export marker (default: script setting "
                             f"EXPORT_JPEG_FOLDER, '{EXPORT_JPEG_FOLDER}'). Overrides "
                             "EXPORT_JPEG_FOLDER.")

    # Force override
    parser.add_argument("--force-transcode", action="store_true",
                        help="Force transcode command")
    parser.add_argument("--force-convert", action="store_true",
                        help="Force convert command")

    # Repair
    parser.add_argument("--repair-jbrd", action="store_true",
                        help="AUDIT/REPAIR mode (no conversion): scan every JXL "
                             "under the input for broken JPEG reconstruction "
                             "(v2.0.0-v2.0.3 wrote XMP markers into jbrd "
                             "containers, which makes djxl --reconstruct_jpeg "
                             "fail for sources that already had XMP). Files "
                             "whose reconstruction fails are fixed by "
                             "stripping those markers and retested. --dry-run "
                             "reports without writing. Exit 1 if any file is "
                             "still broken.")
    parser.add_argument("--auto-repair-jbrd", action="store_true",
                        help="[JXL -> JPEG lossless, djxl >= 0.12] when "
                             "--reconstruct_jpeg fails (the v2.0.0-v2.0.3 jbrd "
                             "marker damage), repair a COPY of the JXL and "
                             "decode from it — the JXL itself is never "
                             "modified (heal the archive with --repair-jbrd). "
                             "The recovered JPEG has identical image data but "
                             "re-serialized XMP, so the delete gate never "
                             "deletes that JXL in the same run.")
    parser.add_argument("--compress-metadata", action="store_true",
                        help="[JPEG -> JXL] store the copied EXIF/XMP Brotli-"
                             "compressed (a few KB smaller per photo) instead of "
                             "plain; IrfanView then shows no EXIF, and "
                             "--repair-jbrd leaves the EXIF layout alone. Same "
                             "as COMPRESS_METADATA_BOXES = True.")
    parser.add_argument("--jpeg-orientation", type=str, default=None,
                        choices=["original", "upright"],
                        help="[JXL -> JPEG] A JXL whose photo was a rotated JPEG comes "
                             "back as the camera wrote it (original: the stored pixel "
                             "order and the original EXIF Orientation; needs "
                             "ImageMagick) or turned upright with Orientation 1 "
                             "(upright). Default: script setting JPEG_ORIENTATION. "
                             "PNG and TIFF outputs are always upright.")

    return parser


# Script settings for every global the CLI mutates, captured at import (after
# every setting above is defined). main() restores THESE at entry — not
# hardcoded literals — so a setting edited at the top of this file survives
# while a previous in-process run's flags do not (round 43).
_RUN_DEFAULTS = {
    "EXPORT_MARKER": EXPORT_MARKER,
    "EXPORT_JPEG_SUBFOLDER": EXPORT_JPEG_SUBFOLDER,
    "EXPORT_JPEG_FOLDER": EXPORT_JPEG_FOLDER,
    "AUTO_REPAIR_JBRD": AUTO_REPAIR_JBRD,
    "COMPRESS_METADATA_BOXES": COMPRESS_METADATA_BOXES,
    "JPEG_ORIENTATION": JPEG_ORIENTATION,
    "DELETE_CONFIRM": DELETE_CONFIRM,
    "DELETE_SOURCE": DELETE_SOURCE,
    "DELETE_SKIPPED": DELETE_SKIPPED,
    "PROVENANCE_CHECK": PROVENANCE_CHECK,
    "OVERWRITE_GUARD": OVERWRITE_GUARD,
    "STORE_MD5": STORE_MD5,
    "TEMP2_DIR": TEMP2_DIR,
}


_work_dir_problem = _common._work_dir_problem


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Every run-scoped global starts from its SCRIPT setting (captured at
    # import) — a second main() in the same process used to inherit the first
    # run's armed flags (the one-way `if args.x:` pattern). Import-time values,
    # never hardcoded literals: a setting the user edited at the top of this
    # file (EXPORT_MARKER, DELETE_CONFIRM, TEMP2_DIR, ...) stays in force.
    for _gname, _gvalue in _RUN_DEFAULTS.items():
        globals()[_gname] = _gvalue
    _DERIVED_INPUT_LABELS.clear()       # per run (_drop_derivative_pairs)

    global RESIZE_MODE, RESIZE_VALUE, ALLOW_UPSCALE
    global SHARPEN, SHARPEN_SIGMA, SHARPEN_GAIN, SHARPEN_THRESHOLD, LABEL_QUALITY
    RESIZE_MODE = None
    RESIZE_VALUE = None
    ALLOW_UPSCALE = False
    SHARPEN = "none"
    LABEL_QUALITY = None
    SHARPEN_SIGMA = None
    SHARPEN_GAIN = None
    SHARPEN_THRESHOLD = None
    _FORCE_REDERIVE.clear()

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
    # A JPEG derivative records its quality in the recipe, so a new quality
    # re-derives it like a new size does (PNG: no quality).
    LABEL_QUALITY = (args.quality if (args.format or "jpeg").lower() in ("jpeg", "jpg")
                     else None)
    SHARPEN_SIGMA = args.sharpen_sigma
    SHARPEN_GAIN = args.sharpen_gain
    SHARPEN_THRESHOLD = args.sharpen_threshold
    if SHARPEN == "none" and any(v is not None for v in
                                 (args.sharpen_sigma, args.sharpen_gain,
                                  args.sharpen_threshold)):
        print("WARNING: --sharpen-sigma/--sharpen-gain/--sharpen-threshold have no "
              "effect without --sharpen screen|print.")

    if not args.input.exists():
        print(f"ERROR: input path does not exist: {args.input}")
        sys.exit(1)

    if args.workers < 1:
        print("ERROR: --workers must be >= 1")
        sys.exit(1)

    # A DRY RUN validates without CREATING: a simulation that leaves a new
    # folder on disk is not a simulation. It never writes into staging, so
    # checking an existing path is enough there.
    if args.staging is not None:
        _stg = Path(args.staging)
        try:
            if args.dry_run:
                if _stg.exists() and not _stg.is_dir():
                    print(f"ERROR: staging directory is not a directory: {args.staging}")
                    sys.exit(1)
            else:
                _stg.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"ERROR: staging directory is not usable: {args.staging} ({e})")
            sys.exit(1)

    # TEMP_DIR and the script-set staging (TEMP2_DIR) are checked up front
    # like in the other three backends: a TEMP_DIR that does not exist used to
    # fail every file mid-run at its first tempfile (and a dry run's marker
    # reads with it). --staging was validated just above.
    for _wd_path, _wd_label, _wd_dry in (
            (TEMP_DIR, "TEMP_DIR", True),
            (TEMP2_DIR if args.staging is None else None, "TEMP2_DIR (staging)", False)):
        _why = _work_dir_problem(_wd_path, _wd_label, args.dry_run, _wd_dry)
        if _why:
            parser.error(_why)

    # --decode only makes sense for JXL inputs; reject it for other single
    # files instead of silently misrouting (e.g. djxl on a PNG).
    if args.decode and args.input.is_file() and args.input.suffix.lower() != '.jxl':
        print(f"ERROR: --decode requires a .jxl input (got {args.input.name})")
        sys.exit(1)

    # --force-transcode means "encode this JPEG to JXL". On a non-JPEG file it
    # used to fail once per file deep inside the worker; reject it up front.
    if (args.force_transcode and not args.decode and args.input.is_file()
            and args.input.suffix.lower() not in JPEG_EXTS):
        print(f"ERROR: --force-transcode requires a JPEG input "
              f"(got {args.input.name}). Use --force-convert for JXL/PNG inputs.")
        sys.exit(2)

    # Range checks the encoder has done all along. Without them --distance 99
    # and --quality 500 sailed through argparse and failed inside cjxl/djxl
    # once PER FILE, with the real cause named nowhere. Exit 2 = "aborted /
    # invalid arguments", matching the documented exit-code table.
    if not 0 <= args.distance <= 25:
        print("ERROR: --distance must be between 0 and 25")
        sys.exit(2)
    if not 1 <= args.quality <= 100:
        print("ERROR: --quality must be between 1 and 100")
        sys.exit(2)

    # Normalize format: jpg -> jpeg
    if args.format == "jpg":
        args.format = "jpeg"

    # Apply configurable export marker before resolving outputs
    global EXPORT_MARKER, EXPORT_JPEG_SUBFOLDER, EXPORT_JPEG_FOLDER, DELETE_CONFIRM, DELETE_SKIPPED, DELETE_SOURCE, PROVENANCE_CHECK, AUTO_REPAIR_JBRD, STORE_MD5, OVERWRITE_GUARD
    global COMPRESS_METADATA_BOXES, JPEG_ORIENTATION
    # Each global was restored to its SCRIPT setting at the top of main()
    # (_RUN_DEFAULTS); the flags override it here, one way.
    if args.export_marker is not None:
        # `is not None` (#433): --export-marker "" means "export NOTHING".
        EXPORT_MARKER = args.export_marker
    if args.auto_repair_jbrd:
        AUTO_REPAIR_JBRD = True
    if args.compress_metadata:
        COMPRESS_METADATA_BOXES = True
    if args.jpeg_orientation is not None:
        JPEG_ORIENTATION = args.jpeg_orientation
    if args.export_subfolder is not None:
        EXPORT_JPEG_SUBFOLDER = args.export_subfolder
    if args.export_jpeg_folder is not None:
        EXPORT_JPEG_FOLDER = args.export_jpeg_folder.strip()
    if args.delete_confirm_off:
        DELETE_CONFIRM = False
    if args.delete_source:
        DELETE_SOURCE = True
    if args.delete_skipped:
        DELETE_SKIPPED = True
    if args.provenance is not None:
        PROVENANCE_CHECK = args.provenance
    if args.no_overwrite_guard:
        OVERWRITE_GUARD = False
    # AFTER both flags are applied: checked before --no-overwrite-guard was
    # read, the combination slipped through and a JPEG was converted and
    # deleted with the guard off (review 261013).
    if not OVERWRITE_GUARD and DELETE_SOURCE:
        # setup_logger() runs inside each cmd_*: print, like the neighbouring
        # validations.
        print("ERROR: The overwrite guard is off (--no-overwrite-guard or "
              "OVERWRITE_GUARD = False) and this run deletes sources "
              "(--delete-source). That combination can overwrite the only "
              "copy of another photo and then delete the file that could "
              "rebuild it, so it is refused. Turn the guard back on for "
              "delete runs.")
        sys.exit(2)
    if args.no_md5:
        STORE_MD5 = False

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
        print("      LOSSY directions (JXL -> JPEG/PNG, lossy encodes): the existing "
              "output must carry")
        print("      THIS source's provenance marker (an unrelated file with the same "
              "name keeps the source).")

    if (args.provenance is not None and not DELETE_SOURCE
            and not (args.sync or args.overwrite)):
        # The --provenance flag is only READ when an existing output is about
        # to be overwritten (--sync/--overwrite, _foreign_overwrite_filter) or
        # a source deleted (--delete-source). This script's default skips every
        # existing output, so armed alone it would sit silently inert while the
        # user believed their archive was guarded — say so (round-39 #413).
        print("WARNING: --provenance has no effect without --delete-source, --sync "
              "or --overwrite: it only checks an existing output's provenance "
              "before that output is overwritten or its source deleted. Nothing "
              "will be checked.")

    # Handle --to-srgb shortcut
    if args.to_srgb:
        args.icc_profile = 'sRGB'
    # Built-in profile names, any case ("srgb", "AdobeRGB", "adobe"...). A
    # lower-case "srgb" used to reach magick as a FILE name and fail per file.
    if args.icc_profile and args.icc_profile.strip().lower() in _ICC_ALIASES:
        args.icc_profile = _ICC_ALIASES[args.icc_profile.strip().lower()]
    elif args.icc_profile and not Path(args.icc_profile).is_file():
        parser.error(f"--icc-profile: not sRGB, AdobeRGB, or an existing file: "
                     f"{args.icc_profile}")
    elif args.icc_profile:
        # A conversion TARGET must be invertible. An input (scanner/camera)
        # profile with A2B tables and no B2A cannot be converted INTO
        # faithfully — the "keep" path already refuses it per file, and the
        # recompressor's --output-icc refuses it up front, like this.
        try:
            _target_bytes = Path(args.icc_profile).read_bytes()
        except OSError as e:
            parser.error(f"--icc-profile {args.icc_profile}: cannot read it ({e})")
        if _icc_a2b_only(_target_bytes):
            parser.error(f"--icc-profile {args.icc_profile}: this profile has A2B "
                         f"tables but no B2A (an input/scanner profile), so nothing "
                         f"can be converted INTO it faithfully — pick an output/"
                         f"working space profile (sRGB, AdobeRGB, ProPhoto, ...)")

    # Determine command
    cmd, auto_decode, reason = determine_command(args.input, args.force_transcode, 
                                                  args.force_convert)

    if cmd == "error":
        print(f"ERROR: {reason}")
        sys.exit(1)

    # ICC conversion is only applied by the CONVERT (re-encode) paths. The
    # lossless transcode recovers the original bytes, so --to-srgb /
    # --icc-profile has nothing to convert there; saying nothing made the flag
    # look honored. Only the pure "transcode" route gets the warning: "auto"
    # converts the JXL pairs WITH the profile (cmd_auto), so the flag is not
    # dead there.
    if args.icc_profile is not None and cmd == "transcode":
        print("WARNING: --to-srgb/--icc-profile has no effect on the lossless "
              "transcode path (the original bytes are recovered). Use "
              "--force-convert for a color-managed re-encode.")

    # A jbrd-carrying JXL is always routed to the LOSSLESS transcode decode,
    # which by definition reproduces the original JPEG — --format/--bit-depth
    # only reach the lossy convert decoder, so the usage example
    # `photo.jxl --format png` silently returned a JPEG (2026-09-21 audit,
    # item 22). The routing itself is correct (jbrd MUST go through
    # transcode), so say the flags are ignored instead of pretending.
    if (cmd == "transcode" and auto_decode and args.input.is_file()
            and (args.format is not None or args.bit_depth is not None)):
        print("WARNING: this JXL carries a jbrd box: the lossless recovery "
              "path always writes the ORIGINAL JPEG, so --format/--bit-depth "
              "are ignored. Use --force-convert for PNG or other re-encoded "
              "output.")

    # Set default mode based on command if not specified
    if args.mode is None:
        if cmd == "transcode":
            args.mode = TRANSCODE_DEFAULT_MODE  # 0 = in-place
        else:
            args.mode = CONVERT_DEFAULT_MODE     # 0 = in-place

    # --export-jpeg-folder is checked once the mode is known: one plain name,
    # not the marker, not the input subfolder (the same rule as the encoder's
    # and the recompressor's --export-jxl-folder).
    if args.export_jpeg_folder is not None:
        if args.mode in (6, 7):
            _why = _validate_export_folder_name(
                EXPORT_JPEG_FOLDER, EXPORT_MARKER,
                EXPORT_JPEG_SUBFOLDER if args.mode == 7 else "")
            if _why:
                parser.error(f"--export-jpeg-folder: {_why}")
        else:
            print(f"WARNING: --export-jpeg-folder only applies to modes 6/7 — ignored "
                  f"in mode {args.mode}.")

    # Mode 1 always writes to <source folder>/<subfolder>/, so an output
    # positional is silently discarded. Accepting it without a word looked like
    # the destination had been honored. Same warning the encoder and decoder
    # have had since v1.9.3; this script never got it.
    if args.mode == 1 and args.output is not None:
        # Naming the folder is the point of the warning, so it must not guess.
        # `auto_decode` alone was wrong: determine_command returns it False for
        # the CONVERT command even when converting a JXL ("JXL without jbrd:
        # convert (lossy decode)"), so a lossy JXL -> JPEG run was told
        # converted_jxl/ while it writes recovered_jpeg/.
        #
        # And for a DIRECTORY input the convert direction is not knowable here
        # at all: cmd_convert scans first and falls back to from_jxl only when
        # the folder holds no JPEG/PNG. Say so rather than pick one.
        if cmd == "transcode":
            _folder = (RECOVERED_JPEG_FOLDER if auto_decode else CONVERTED_JXL_FOLDER) + "/"
        elif args.output_name != CONVERT_OUTPUT_FOLDER:
            _folder = args.output_name + "/"    # --output-name wins in convert 1/3
        elif args.input.is_file() or args.decode:
            _dec = args.decode or args.input.suffix.lower() == ".jxl"
            _folder = (RECOVERED_JPEG_FOLDER if _dec else CONVERTED_JXL_FOLDER) + "/"
        else:
            _folder = (f"{CONVERTED_JXL_FOLDER}/ or {RECOVERED_JPEG_FOLDER}/, "
                       f"whichever this folder's content selects")
        print(f"WARNING: --mode 1 ignores the output folder ({args.output}): outputs go "
              f"to <source folder>/{_folder}. Use --mode 2 to write everything into "
              f"one folder.")

    # Modes 3-8 compute their destinations from each source's folder; only
    # modes 0 and 2 honor an output positional (the recompressor's rule).
    # Accepting it without a word made the flag look honored — a dry run
    # showed a clean plan while every output went to the mode's own layout.
    if args.mode not in (0, 1, 2) and args.output is not None:
        print(f"WARNING: the output positional is only honored in modes 0 and 2 "
              f"(mode {args.mode} computes its own folders) — ignoring it")

    # Modes 6/7 anchor on an EXPORT folder and scan recursively; over a single
    # FILE the scan yields nothing and the run exits 0 having done nothing.
    if args.mode in (6, 7) and args.input.is_file():
        print(f"ERROR: --mode {args.mode} needs a DIRECTORY: it scans the folder "
          f"tree for the export marker. Got a file.")
        sys.exit(2)

    # Repair mode: audit/fix broken jbrd reconstruction. Routed before the
    # FULL tool check below because it neither converts nor cares about the
    # input's file type; it only needs djxl >= 0.12 and exiftool — requiring
    # cjxl too refused a repair on a machine that can perfectly well run one.
    if args.repair_jbrd:
        if not _tool_at_least("djxl", 0, 12):
            print("ERROR: --repair-jbrd requires djxl >= 0.12 "
                  "(djxl --reconstruct_jpeg)")
            sys.exit(2)
        if shutil.which(_get_exiftool_cmd()) is None:
            print("ERROR: --repair-jbrd requires exiftool in PATH")
            sys.exit(1)
        sys.exit(cmd_repair_jbrd(args))

    # Resize/sharpening write a DERIVATIVE: it never replaces or deletes its
    # source, and the lossless JPEG recovery cannot shape pixels (the original
    # bytes are reproduced verbatim).
    if _is_derivative() and (DELETE_SOURCE or args.delete_skipped):
        parser.error("--resize-*/--sharpen write a DERIVATIVE: it never replaces "
                     "or deletes its source — drop --delete-source/--delete-skipped")
    # A colour conversion is a derivative too (W6/T4 of the 2026-10-08 audit):
    # an sRGB JPEG of a 16-bit wide-gamut master is not a replacement for it.
    # The recompressor's --output-icc never deletes, and the wrapper's
    # manifest already suppressed --delete-source for an OutputICC row — while
    # this direct route converted the gamut and deleted the master. Only the
    # transcode route ignores the profile (the original bytes come back).
    if (args.icc_profile and cmd != "transcode"
            and (DELETE_SOURCE or args.delete_skipped)):
        parser.error("--icc-profile/--to-srgb writes a colour-converted DERIVATIVE: "
                     "it never deletes its source — drop --delete-source/"
                     "--delete-skipped (or the conversion)")
    # T-B (owner's option 1): an explicit lossy decode never deletes the JXL —
    # a JPEG is lossy, and an 8-bit PNG may be a degraded copy (per file, the
    # gates keep those too: _lossy_decode_keep_reason).
    if (cmd == "convert" and args.decode
            and (DELETE_SOURCE or args.delete_skipped)
            and ((args.format or "jpeg") in ("jpeg", "jpg")
                 or (args.bit_depth or PNG_DEFAULT_BIT_DEPTH) != 16)):
        parser.error("a JXL -> JPEG (or 8-bit PNG) conversion is not a copy of the JXL: "
                     "it never deletes its source — drop --delete-source/"
                     "--delete-skipped, decode to a 16-bit PNG, or use --force-transcode "
                     "for the bit-exact recovery of JPEG archives")
    if _is_derivative() and (cmd == "transcode" or (cmd == "auto" and args.decode)):
        parser.error("a bit-exact JPEG reconstruction cannot be resized/sharpened — "
                     "drop --force-transcode/--decode (or the resize/sharpening flags)")

    # Required tools, once, before any cmd_* runs. Without it a missing
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
        # (0, 11, 2), not (0, 11): the message has always named 0.11.2 as the
        # minimum, and the encoder and decoder both test the patch level. This
        # copy tested only major.minor, so 0.11.0 and 0.11.1 passed in silence
        # HERE and were warned about by the other two.
        for _exe in ("cjxl", "djxl"):
            _v = _tool_version(_exe)
            if _v is not None and _v[:3] < (0, 11, 2):
                print(f"WARNING: {_exe} {'.'.join(map(str, _v))} is older than the "
                      f"supported minimum (0.11.2); conversions may fail in "
                      f"confusing ways. https://github.com/libjxl/libjxl/releases")

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

    _planned_outputs.clear()        # run-scoped (#615)

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