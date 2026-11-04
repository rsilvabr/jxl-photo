#!/usr/bin/env python3
"""
jxl_photo_v2.py - Interactive wrapper with Auto Mode
Adds [A] Auto Mode that analyzes folder structure and recommends best options.
Based on jxl_photo.py - all original features preserved.
"""

import argparse
import importlib
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext, redirect_stderr
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Who wrote a file: recorded in every output's xmpMM:History and printed by
# --version. NOT a user setting. Bumped in ALL FIVE scripts on every release,
# even when only one script changed — tests/test_toolkit_version.py pins the
# five to each other and to the newest section of docs/version_history.md.
TOOLKIT_NAME = "jxl-photo"
TOOLKIT_VERSION = "2.22.0_beta1"
TOOLKIT_URL = "https://github.com/rsilvabr/jxl-photo"
# Named in the menu's tip when Windows Explorer has no JXL thumbnail handler.
JXL_THUMB_URL = "https://github.com/rsilvabr/jxl-thumb"

# Initialize wrapper logger early so manifest/path validation code can log safely.
# Without a handler these messages would hit Python's lastResort (raw stderr,
# unformatted, outside the rich flow).
logger = logging.getLogger("jxl_photo")
if not logger.handlers:
    _lh = logging.StreamHandler()
    _lh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(_lh)

# The wrapper prints unicode icons (✓/✗/⚠); on legacy Windows consoles or
# redirected stdout (cp1252) they would crash with UnicodeEncodeError.
# Reconfigure stdout to tolerate them (the backend scripts do the same).
#
# encoding AND errors, not errors alone: a real console already hands Python
# UTF-8, but a REDIRECTED stdout falls back to the ANSI codepage, where every
# one of those icons is unencodable. errors="replace" then turned ✓ and ✗ into
# the SAME "?" — so the dependency bar in a scheduled-task log said nothing at
# all, which is the one place its whole job is to be read. The backend scripts
# already pass encoding here; this comment claimed as much while the wrapper
# did not.
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.box import SIMPLE as BOX_SIMPLE
    from rich.prompt import Prompt, IntPrompt, Confirm
    from rich.markup import escape as _markup_escape
    RICH_AVAILABLE = True
    # No force_terminal: when stdout is redirected to a file/pipe, rich must
    # not emit ANSI escape sequences.
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

    def _markup_escape(text):
        return text


def _rich_safe(value) -> str:
    """User text (a path, a folder or preset name, a message that carries one)
    made safe inside a rich markup string. rich reads a bracketed word starting
    with a lowercase letter as a style tag and drops it: 'E:\\Fotos [final]'
    was shown as 'E:\\Fotos ' in the summary and the 'Executing:' line."""
    return _markup_escape(str(value))


# Backport of Path.is_relative_to for Python < 3.9
def _is_relative_to(path: Path, anchor: Path) -> bool:
    """Return True if path is relative to anchor (Python < 3.9 compatible)."""
    try:
        path.relative_to(anchor)
        return True
    except ValueError:
        return False


def _display_cmd(cmd: List) -> List[str]:
    """Command line for DISPLAY only: omits --delete-confirm-off so a
    copy-pasted command always keeps the child's own confirmation gate
    (the actual subprocess call keeps the real flag)."""
    return [a for a in cmd if a != "--delete-confirm-off"]


def _split_expert_flags(flags: str) -> List[str]:
    """Split a free-form expert-flags string into argv tokens.

    shlex posix=False keeps Windows backslashes intact AND groups quoted
    segments, but retains the surrounding quote characters — so we strip
    balanced surrounding quotes from each token afterwards. This handles
    both quoted paths with spaces (--staging "E:\\my dir\\x") and plain
    backslash paths (--staging E:\\temp_jxl).
    """
    import shlex
    try:
        tokens = shlex.split(flags, posix=False)
    except ValueError:
        tokens = flags.split()
    stripped = []
    for t in tokens:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'"):
            t = t[1:-1]
        stripped.append(t)
    return stripped


def _flags_request_delete(flags: Optional[str]) -> bool:
    """True when the free-form expert-flags string asks the child to delete sources.

    Expert flags are appended to the child's argv verbatim, so `--delete-source`
    typed here NEVER sets advanced_options['delete_source'] — the wrapper's whole
    delete-gate logic keys off that dict and so never fires. Paired with
    `--delete-confirm-off` (which suppresses the child's OWN confirmation) a saved
    preset could delete originals from Task Scheduler with nobody confirming
    anything, the exact scenario the unattended gate exists to prevent.

    argparse also lets a later `--mode 8` in these flags override the mode the
    wrapper passed, so the callers deliberately do NOT combine this with a mode
    check: expert-flag deletion is gated on its own, whatever the stored mode says.
    """
    # The children run argparse with allow_abbrev=True, so any UNAMBIGUOUS
    # prefix of --delete-source also deletes: `--delete-s` works, and paired
    # with `--delete-c` (an abbreviation of --delete-confirm-off) a preset
    # would sail past a gate that only matched the full spelling. Tokens that
    # are also a prefix of --delete-confirm-off (`--delete`, `--delete-`)
    # are ambiguous and argparse rejects them, so they are not flagged here.
    source_spellings = ('--delete-source', '--delete_source')
    confirm_spellings = ('--delete-confirm-off', '--delete_confirm_off')
    # --delete-skipped exists now, so a prefix like `--delete-s` matches BOTH it
    # and --delete-source. argparse rejects an ambiguous abbreviation outright,
    # so such a token can never delete anything — treating it as a delete
    # request would charge the HHMM token for a run that then dies on
    # "ambiguous option". _flags_ambiguous_delete reports those separately so
    # the caller can refuse before asking for the token.
    skipped_spellings = ('--delete-skipped', '--delete_skipped')
    for t in _split_expert_flags(flags or ''):
        # `--delete-source=1` is NOT a delete request: the children declare
        # these flags store_true, so argparse rejects an explicit argument
        # (exit 2) and nothing runs. _flags_ambiguous_delete reports the token
        # so the caller refuses up front instead of charging the HHMM token
        # for a doomed run.
        if '=' in t:
            continue
        tok = t
        if not tok.startswith('--'):
            continue
        if any(c.startswith(tok) for c in confirm_spellings):
            continue        # ambiguous with --delete-confirm-off; argparse refuses
        if any(c.startswith(tok) for c in skipped_spellings) and not any(
                s == tok for s in source_spellings):
            # Matches --delete-skipped too. Only an EXACT --delete-source spelling
            # survives this: anything shorter is ambiguous and cannot run.
            continue
        for spelling in source_spellings:
            if spelling.startswith(tok):
                return True
    return False


def _flags_ambiguous_delete(flags: Optional[str]) -> Optional[str]:
    """The first expert-flag token the child's argparse would reject, or None.

    `--delete-s` matches both --delete-source and --delete-skipped, so argparse
    exits with "ambiguous option" and the child never runs. `--delete-source=1`
    is rejected just as hard: the delete flags are store_true and take no
    explicit argument. Caught up front, the user is told to spell it out
    instead of typing a token for a doomed run.
    """
    families = ('--delete-source', '--delete_source',
                '--delete-skipped', '--delete_skipped',
                '--delete-confirm-off', '--delete_confirm_off')
    for t in _split_expert_flags(flags or ''):
        tok = t.split('=', 1)[0]
        if not tok.startswith('--'):
            continue
        if '=' in t:
            # An explicit argument on a store_true flag: exit 2 whether the
            # base spelling is exact or an unambiguous abbreviation.
            if any(f.startswith(tok) for f in families):
                return t
            continue
        if tok in families:
            continue
        if sum(1 for f in families if f.startswith(tok)) > 1:
            return tok
    return None


def _flags_set_mode(flags: Optional[str]) -> Optional[str]:
    """The expert-flag token that would set the child's --mode, or None.

    The wrapper decides everything around a run from ITS mode: the in-place
    and delete gates, the collision scan, the counts in the confirmation. A
    `--mode 8` in the expert flags wins in the child's argparse (it comes
    last), so the run would replace files in place behind a gate that saw
    another mode (W-B of the 2026-11-03 audit). `--mo`/`--mod` count too:
    argparse accepts an unambiguous abbreviation (the decoder has no other
    --mo... flag)."""
    for t in _split_expert_flags(flags or ''):
        tok = t.split('=', 1)[0]
        if tok.startswith('--') and len(tok) >= 4 and '--mode'.startswith(tok):
            return t
    return None


def _expert_flags_problem(origin: str, dest: str, flags: Optional[str]) -> Optional[str]:
    """Why the expert flags cannot run on this direction's script, or None.

    Checked against the child's own command line (its build_parser(), read
    in-process with parse_known_args) BEFORE the run and before any HHMM
    token: a typo, a flag of another script or a value outside its choices
    used to reach the child, which exited 2 after the wrapper had asked for
    the token. When the child cannot be imported nothing is checked here and
    the child decides, as before."""
    tokens = _split_expert_flags(flags or '')
    if not tokens or not origin or not dest:
        return None
    module = _child_module(origin, dest)
    try:
        parser = importlib.import_module(module).build_parser()
    except Exception:
        return None
    err = io.StringIO()
    try:
        with redirect_stderr(err):
            _ns, extras = parser.parse_known_args(["__jxlphoto_probe__"] + tokens)
    except SystemExit:
        last = [ln for ln in err.getvalue().strip().splitlines() if ln.strip()]
        why = last[-1].split("error: ", 1)[-1] if last else "invalid"
        return f"{module}.py rejects them: {why}"
    unknown = [t for t in extras if t.startswith('-')]
    if unknown:
        return f"{' '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not an option of {module}.py"
    return None


def _strip_surrounding_quotes(value: str) -> str:
    """Strip balanced surrounding quotes from pasted paths
    (e.g. Explorer's 'Copy as path' wraps the path in double quotes)."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _sane_distance(value: Any, fallback: float = 0.1) -> float:
    """Clamp any stored/typed distance into cjxl's valid 0.0-15.0 range.

    The config file is plain JSON the user can hand-edit, and _load_config does
    no type checking — a distance that came back as "0,05" or None would other-
    wise reach an f-string format spec or the cjxl command line. Anything
    uninterpretable falls back instead of aborting the menu."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if result != result:  # NaN: float('nan') fails every comparison below
        return fallback
    return max(0.0, min(result, 15.0))


# The numbers a saved workflow replays straight into the child's argv, with the
# range each child's argparse accepts. Distance is deliberately absent: it has
# _sane_distance, which clamps instead of refusing — the right call for a
# quality knob with a meaningful fallback, the wrong one for the values below.
_SESSION_NUMBERS = (
    ("last_workers", "workers", 1, 32),
    ("last_effort", "effort", 1, 10),
    ("last_quality", "quality", 1, 100),
)
# Modes 0-8, plus 99 for a manifest run.
_SESSION_MODES = frozenset(range(0, 9)) | {99}

# The hand-editable session fields that are replayed VERBATIM into the child's
# argv (or pick its code path), each with the values the wizard can produce.
# Anything else is a hand-edit typo that would otherwise reach the CHILD's
# argparse as a command line the user never typed.
_SESSION_CHOICES = {
    "last_provenance": ("path", "content", "adopt"),
    "last_multipage_mode": ("ignore", "skip", "split", "split_all"),
    "last_compression": ("zip", "lzw", "none"),
    "last_depth_policy": ("force16", "preserve_thumbnails", "preserve_original"),
    "last_conversion_type": (
        "transcode_lossless", "convert_lossy",
        "jxl_tiff_encoder", "jxl_tiff_encoder_lossless", "jxl_tiff_decoder",
        "jxl_to_jpeg_auto", "jxl_to_jpeg_lossless", "jxl_to_jpeg_force",
        "jxl_to_png", "jxl_recompress",
    ),
    # The wizard's only ICC choice is the sRGB conversion.
    "last_icc_profile": ("srgb",),
}
# --depth / --bit-depth are argparse choices=[8, 16] in every child.
_SESSION_BIT_DEPTHS = (8, 16)
# The wizard's Step 1/2 formats. These two fields pick WHICH child script a
# replayed session runs, so they are validated case-SENSITIVELY (unlike
# _SESSION_CHOICES): a hand-edited "Tiff" would survive a lowercased check
# and then fail every `== 'tiff'` comparison downstream, silently routing
# the run to the wrong script while the panel says TIFF.
_SESSION_ORIGIN_FORMATS = ("jpeg", "tiff", "jxl")
_SESSION_DEST_FORMATS = ("jpeg", "tiff", "jxl", "png")


def _as_exact_int(raw: Any) -> int:
    """int() that also accepts 7.0 and "7", but refuses "7.5", NaN and "sete"."""
    value = float(raw)                      # ValueError/TypeError on garbage
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(raw)
    if value != int(value):
        raise ValueError(raw)
    return int(value)


def _session_number_error(session: Dict) -> Optional[str]:
    """Describe the first corrupt value in a stored workflow, or None if sane.

    The config is plain JSON the user can hand-edit and _load_config does no
    type checking, so a saved workflow can come back with a mode of "sete".
    That reached int() and ended the run in a raw ValueError traceback; the
    other values reached the CHILD's argparse instead, which then complained
    about a command line the user never typed.

    Corrupt numbers are REFUSED, not defaulted: a workflow replayed unattended
    must never quietly run with a mode — or a worker count — that nobody chose.
    Mode especially, since it decides where the outputs land.
    """
    raw_mode = session.get("last_output_mode")
    if raw_mode not in (None, ""):
        try:
            mode = _as_exact_int(raw_mode)
        except (TypeError, ValueError):
            return f"mode is not a number: {raw_mode!r}"
        if mode not in _SESSION_MODES:
            return f"mode {mode} does not exist (valid: 0-8, or 99 for a manifest)"

    for field, label, low, high in _SESSION_NUMBERS:
        raw = session.get(field)
        if raw in (None, ""):
            continue
        try:
            value = _as_exact_int(raw)
        except (TypeError, ValueError):
            return f"{label} is not a number: {raw!r}"
        if not low <= value <= high:
            return f"{label} is out of range ({low}-{high}): {value}"

    # Same refuse-corrupt rule for the enumerated fields: they go straight onto
    # the child's command line, where a typo surfaces as an argparse rejection
    # of a command the user never typed.
    raw_depth = session.get("last_bit_depth")
    if raw_depth not in (None, ""):
        try:
            depth = _as_exact_int(raw_depth)
        except (TypeError, ValueError):
            return f"bit depth is not a number: {raw_depth!r}"
        if depth not in _SESSION_BIT_DEPTHS:
            return f"bit depth is not a valid value: {depth} (valid: 8/16)"

    for field, allowed in _SESSION_CHOICES.items():
        raw = session.get(field)
        if raw in (None, ""):
            continue
        if not isinstance(raw, str) or raw.lower() not in allowed:
            return (f"{field[len('last_'):]} is not a known value: {raw!r} "
                    f"(valid: {'/'.join(allowed)})")

    # The two fields that pick WHICH script runs. Case-sensitive on purpose
    # (see the constants): accepting "Tiff" here would route the replay to
    # the transcoder on a TIFF tree while the panel says TIFF -> JXL.
    for field, allowed in (("last_origin_format", _SESSION_ORIGIN_FORMATS),
                           ("last_dest_format", _SESSION_DEST_FORMATS)):
        raw = session.get(field)
        if raw in (None, ""):
            continue
        if not isinstance(raw, str) or raw not in allowed:
            return (f"{field[len('last_'):]} is not a known value: {raw!r} "
                    f"(valid: {'/'.join(allowed)})")

    # Same refuse-corrupt rule for a free-form string field that reaches the
    # child's command line (#319e pattern): a hand-edited
    # last_exclude_folders holding a number or a list must not become
    # '--exclude-folders <garbage>' on a replayed workflow. Empty string and
    # None both read as "no exclusion", so they pass. The entries must also
    # be folder NAMES — the same shape rule the children enforce on the CLI
    # (exit 2 there; refused here instead, before anything runs).
    raw_excl = session.get("last_exclude_folders")
    if raw_excl not in (None, ""):
        if not isinstance(raw_excl, str):
            return f"exclude_folders is not a string: {raw_excl!r}"
        if any(("\\" in n) or ("/" in n)
               for n in raw_excl.split(";") if n.strip()):
            return ("exclude_folders takes folder NAMES, not paths: "
                    f"{raw_excl!r}")
    return None


def _session_int(session: Dict, field: str, fallback: int) -> int:
    """A stored workflow number as a real int.

    _session_number_error accepts 7.0 and "7" on purpose — Excel and
    hand-edited JSON both produce them — but every consumer then did a plain
    int()/str() on the raw value: `int("8.0")` raises ValueError (a traceback
    out of a repeat/preset run), and `--workers 8.0` reached the CHILD's
    argparse as a command line the user never typed. Validate there, coerce
    here, so the two can never disagree again.

    Anything this returns has already passed _session_number_error on the
    normal paths; the fallback only covers absent/blank fields.
    """
    raw = session.get(field)
    if raw in (None, ""):
        return fallback
    try:
        return _as_exact_int(raw)
    except (TypeError, ValueError):
        return fallback


def _replace_suffix_token(name: str, suffix_from: str, suffix_to: str) -> str:
    """Replace the FIRST occurrence of suffix_from in a folder name, but only
    when it is a complete token (bounded by _, -, space, or string edges) —
    otherwise 'MyJXLArchive' would become 'MyTIFFArchive'. No token match
    returns the name unchanged (caller applies the append fallback).
    Same rule as the three backend scripts.
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


def _marker_matches(part_lower: str, marker_lower: str) -> bool:
    """Folder-name part matches the export marker.

    Matches start/end with the marker (e.g. _EXPORT, _Export_2024, My_EXPORT)
    and, for underscore-wrapped markers, also the bare word — so the default
    '_EXPORT' also detects 'Export_Lightroom' and 'Lightroom_Export', as the
    documentation promises. Same rule as the three backend scripts.
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


def _path_excluded_below_root(file_path: Path, root, names) -> bool:
    """True when any folder segment of file_path BELOW root matches one
    of `names` (lower-cased). Relative to root, segment match,
    fail-closed."""
    if not names or root is None:
        return False
    try:
        rel = file_path.parent.relative_to(root)
    except ValueError:
        try:
            rel = file_path.parent.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            rel = file_path.parent  # fail closed, like _is_tool_output_path
    return any(seg.lower() in names for seg in rel.parts)


def _append_expert_flags(cmd: List[str], flags: str) -> None:
    """Append free-form expert flags to a child command line.

    When the flags ask to delete sources, the wrapper charges the HHMM token
    itself (see _flags_request_delete) — so the child's OWN confirmation must
    be suppressed too, or it re-prompts on the inherited stdin mid-stream and
    the run appears to hang. Appended AFTER the flags so argparse's
    last-one-wins cannot be subverted by an earlier --delete-confirm in the
    flags (there is no such flag today; the position is simply the safe one).
    """
    if not flags:
        return
    tokens = _split_expert_flags(flags)
    cmd.extend(tokens)
    if _flags_request_delete(flags) and not any(
            t.split('=', 1)[0] in ('--delete-confirm-off', '--delete_confirm_off')
            for t in tokens):
        cmd.append('--delete-confirm-off')


def _is_manifest_header_row(row) -> bool:
    """True only when the first row really is the CSV header — not when a
    data folder happens to be named 'source'. A header names at least one of
    the other columns."""
    if not row or row[0].strip().lower() != "source":
        return False
    return any(cell.strip().lower() == expected
               for cell, expected in zip(row[1:4], ("destination", "mode", "direction")))


# Optional per-row columns after Direction. Empty cell = the option is NOT
# applied on that row; an ABSENT column falls back to the wizard's answer.
# The export columns are the exception: an empty cell keeps the RUN's value
# (marker/subfolder/folder), so filling one row never resets the others.
_MANIFEST_OPTION_COLUMNS = ("outputicc", "resize", "sharpen", "renamefrom",
                            "renameto", "exportmarker", "exportsubfolder",
                            "exportjxlfolder", "excludefolders")
# The derivative/rename columns: the directions whose child writes a
# derivative. The manifest generator writes those five columns for exactly
# these directions.
_MANIFEST_DERIVATIVE_DIRECTIONS = frozenset({"jxl2jxl", "jxl2jpeg", "jxl2png"})
_MANIFEST_ALL_DIRECTIONS = frozenset(
    {"tiff2jxl", "jxl2tiff", "jpeg2jxl", "jxl2jpeg", "jxl2png", "jxl2jxl"})
_MANIFEST_OPTION_DIRECTIONS = {
    "outputicc":       _MANIFEST_DERIVATIVE_DIRECTIONS,
    "resize":          _MANIFEST_DERIVATIVE_DIRECTIONS,
    "sharpen":         _MANIFEST_DERIVATIVE_DIRECTIONS,
    "renamefrom":      _MANIFEST_DERIVATIVE_DIRECTIONS,
    "renameto":        _MANIFEST_DERIVATIVE_DIRECTIONS,
    "exportmarker":    _MANIFEST_ALL_DIRECTIONS,
    "exportsubfolder": _MANIFEST_ALL_DIRECTIONS,
    "exportjxlfolder": frozenset({"tiff2jxl", "jxl2jxl"}),
    # --exclude-folders lives in the TIFF encoder and decoder only.
    "excludefolders":  frozenset({"tiff2jxl", "jxl2tiff"}),
}
_MANIFEST_OPTION_DISPLAY = {"outputicc": "OutputICC", "resize": "Resize",
                            "sharpen": "Sharpen", "renamefrom": "RenameFrom",
                            "renameto": "RenameTo",
                            "exportmarker": "ExportMarker",
                            "exportsubfolder": "ExportSubfolder",
                            "exportjxlfolder": "ExportJxlFolder",
                            "excludefolders": "ExcludeFolders"}
_MANIFEST_RESIZE_RE = re.compile(r"^(?:(long|short):(\d+)|(\d+(?:\.\d+)?)%)(\+up)?$",
                                 re.IGNORECASE)
_MANIFEST_RENAME_BAD = re.compile(r'[,;/\\:*?"<>|]')


def _parse_manifest_row_options(cells: Dict[str, str], direction: str,
                                anchor) -> Tuple[Optional[Dict], Optional[str]]:
    """(options, None) or (None, reason). `cells` maps the lower-case column
    name to the raw cell text, for the columns PRESENT in the CSV only.
    Returned keys, only for present columns: output_icc (str|None),
    resize_mode/resize_value/allow_upscale, sharpen, rename_from, rename_to —
    None / 'none' / '' meaning 'not applied'.     The export columns
    (export_marker, export_subfolder, export_jxl_folder) are str and appear
    ONLY when the cell has a value (empty keeps the run's value).
    `exclude_folders` (ExcludeFolders) is a hybrid: '' keeps the run's
    value (key absent), '-'/'none' means "no exclusion on this row"
    (exclude_folders=None, PRESENT), a value is the raw ';'-separated
    string (the run's value with entries for this row). Any ';' entry
    containing '\\' or '/' refuses the whole manifest — folder NAMES, not
    paths."""
    opts: Dict = {}
    for name, raw in cells.items():
        value = (raw or "").strip()
        if direction not in _MANIFEST_OPTION_DIRECTIONS[name]:
            if value:
                return None, (f"column {_MANIFEST_OPTION_DISPLAY[name]} is not "
                              f"supported for {direction} manifests")
            continue
        if name == "outputicc":
            if not value:
                opts["output_icc"] = None
                continue
            key = value.lower()
            if key == "srgb":
                opts["output_icc"] = "sRGB"
            elif key in ("adobergb", "adobe", "adobergb1998"):
                opts["output_icc"] = "AdobeRGB"
            else:
                p = value.strip('"')
                if ".." in Path(p).parts:
                    return None, f"column OutputICC: '..' is not allowed ({value!r})"
                resolved = anchor(p)
                if not Path(resolved).is_file():
                    return None, (f"column OutputICC: not sRGB, AdobeRGB, or an "
                                  f"existing file: {value!r}")
                opts["output_icc"] = resolved
        elif name == "resize":
            if not value:
                opts["resize_mode"] = None
                opts["resize_value"] = None
                opts["allow_upscale"] = False
                continue
            m = _MANIFEST_RESIZE_RE.match(value)
            if not m:
                return None, (f"column Resize: {value!r} is not long:N, short:N "
                              f"or P% (optional +up suffix)")
            allow_up = bool(m.group(4))
            if m.group(1):
                resize_mode, resize_value = m.group(1).lower(), int(m.group(2))
                if resize_value <= 0:
                    return None, f"column Resize: {value!r} — N must be > 0"
            else:
                resize_mode, resize_value = "percent", float(m.group(3))
                if resize_value <= 0:
                    return None, f"column Resize: {value!r} — P must be > 0"
                if resize_value > 100 and not allow_up:
                    return None, (f"column Resize: {value!r} would upscale — "
                                  f"add +up")
            opts["resize_mode"] = resize_mode
            opts["resize_value"] = resize_value
            opts["allow_upscale"] = allow_up
        elif name == "sharpen":
            v = value.lower()
            if not v or v == "none":
                opts["sharpen"] = "none"
            elif v in ("screen", "print"):
                opts["sharpen"] = v
            else:
                return None, f"column Sharpen: {value!r} is not none/screen/print"
        elif name == "renamefrom":
            if _MANIFEST_RENAME_BAD.search(value) or ".." in value:
                return None, (f"column RenameFrom: path characters are not "
                              f"allowed ({value!r})")
            opts["rename_from"] = value
        elif name == "renameto":
            if _MANIFEST_RENAME_BAD.search(value) or ".." in value:
                return None, (f"column RenameTo: path characters are not "
                              f"allowed ({value!r})")
            opts["rename_to"] = value
        elif name in ("exportmarker", "exportsubfolder", "exportjxlfolder"):
            if not value:
                continue  # empty = this row keeps the run's value
            if (_MANIFEST_RENAME_BAD.search(value) or ".." in value
                    or value.strip(". ") == ""):
                return None, (f"column {_MANIFEST_OPTION_DISPLAY[name]}: must be a "
                              f"plain folder name ({value!r})")
            key = {"exportmarker": "export_marker",
                   "exportsubfolder": "export_subfolder",
                   "exportjxlfolder": "export_jxl_folder"}[name]
            opts[key] = value
        elif name == "excludefolders":
            # '' keeps the run's value (key absent; the export* convention).
            # '-' / 'none' are explicit removals for this row
            # (exclude_folders=None, PRESENT) — the wizard's "no exclusion"
            # answer spelled out.
            if value:
                if value.lower() != "none" and value != "-":
                    entries = [n.strip() for n in value.split(";") if n.strip()]
                    if any("\\" in n or "/" in n for n in entries):
                        return None, (f"column ExcludeFolders takes folder "
                                      f"NAMES, not paths ({value!r})")
                    if entries:
                        opts["exclude_folders"] = value
                    # A ';' / ';;' / whitespace-only cell filters NOTHING in
                    # the children, so it reads as an EMPTY cell: the row
                    # keeps the run's value (key absent, the export*
                    # convention), it does not silently erase a run-level
                    # exclusion for this row.
                else:
                    opts["exclude_folders"] = None
    if opts.get("rename_to") and not opts.get("rename_from"):
        return None, "column RenameTo is set but RenameFrom is empty on the same row"
    return opts, None


# Manifest modes whose child walks the WHOLE tree under the Source. Modes 0 and
# 1 are flat in all three children (find_files_mode0 / find_jxls_flat /
# find_jpegs_flat), so a Source nested inside another Source there can never
# hand the same file to two child processes.
_RECURSIVE_MANIFEST_MODES = frozenset({2, 3, 4, 5, 6, 7, 8})

# Modes whose output path DROPS folder structure, so two sources in different
# folders resolve to the same output and an archive can be overwritten by an
# unrelated photo. Mirrors _COLLAPSING_MODES in jxl_tiff_encoder.py.
_COLLAPSING_MODES = frozenset({2, 4, 5, 6, 7})


def _recompress_entry_in_place(source, dest_path, mode) -> bool:
    """Does a JXL -> JXL (recompressor) run REPLACE its sources?

    Mode 8 always does (the output is the source path). Mode 0 does when it
    has no output folder — or when that folder IS the source folder, which is
    what every manifest row sends: the loader fills an empty Destination cell
    with the Source, so testing "no Destination" alone never matched a
    manifest entry, and the in-place gate was silently skipped.
    """
    if mode == 8:
        return True
    if mode != 0:
        return False
    if not dest_path:
        return True
    try:
        src = Path(source)
        if src.is_file():
            # A manifest row may carry the FILE itself as Destination (the
            # loader copies a one-column Source into that cell). Comparing it to
            # the parent never matched, the output positional was emitted, and
            # the recompressor computed `<file>\<name>` — the entry never ran.
            if (os.path.normcase(os.path.abspath(str(dest_path)))
                    == os.path.normcase(os.path.abspath(str(src)))):
                return True
        src_dir = src.parent if src.is_file() else src
        return (os.path.normcase(os.path.abspath(str(dest_path)))
                == os.path.normcase(os.path.abspath(str(src_dir))))
    except (OSError, ValueError):
        return True     # cannot tell -> treat as destructive (fail closed)


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


def _supports_provenance_adopt(origin: str, dest: str) -> bool:
    """Does the child script for this direction accept --provenance adopt?

    Only jxl_tiff_encoder.py does. Its adopt scan decodes each unmarked JXL and
    compares it against the TIFF that would overwrite it — a real proof of the
    pairing, which is what makes stamping the marker safe rather than a rubber
    stamp. jxl_tiff_decoder.py and jxl_jpeg_transcoder.py declare
    `choices=["path", "content"]`, so passing adopt to either is not a weaker
    check: it is argparse exit 2 before a single file is read, and inside a
    manifest it takes every remaining entry down with it.

    This gates the OFFER, not just the emission — a menu entry the target
    cannot accept is worse than no menu entry at all.
    """
    return origin == 'tiff' and dest == 'jxl'


# The subfolder global each child filters modes 6/7 by. Named per direction, so
# every site that borrows a child's resolver has to set the right one — which is
# exactly what the two sites below used to disagree about.
_CHILD_SUBFOLDER_GLOBALS = ('EXPORT_TIFF_SUBFOLDER', 'EXPORT_JXL_SUBFOLDER',
                            'EXPORT_JPEG_SUBFOLDER')


@contextmanager
def _with_child_marker(child, marker: Optional[str], subfolder: Optional[str] = None,
                       jxl_folder: Optional[str] = None,
                       exclude_folders: Optional[str] = None):
    """Run a child's own finder/resolver under THIS run's marker, subfolder,
    modes 6/7 output folder and --exclude-folders list.

    The children read `EXPORT_MARKER`, `EXPORT_*_SUBFOLDER`,
    `EXPORT_JXL_FOLDER` and `EXCLUDE_FOLDERS` as module globals, set from CLI
    flags. The wrapper imports those modules in-process to reuse their path
    logic, so it has to mirror the flags by assignment — and then put them
    back, because this process outlives the call.

    One context manager instead of two hand-written blocks, because the two had
    already drifted in both directions:

      * `_count_origin_files` applied the marker but NOT the subfolder, so the
        delete confirmation counted every subfolder under the marker while the
        run converts one — a different FILE SET, under the panel whose visible
        count is what catches a wrong folder before the HHMM token;
      * `_manifest_output_collisions` applied both and restored NEITHER, leaking
        them into every later use in the same menu session.

    `jxl_folder` matters for the same reason: a custom `--export-jxl-folder`
    moves the outputs, so the collision mirror and the origin count must see
    the same destination the child will write.

    Restores through `finally`, so a resolver that raises cannot leave the
    module rewritten either.
    """
    saved = {}
    try:
        if marker and hasattr(child, 'EXPORT_MARKER'):
            saved['EXPORT_MARKER'] = child.EXPORT_MARKER
            child.EXPORT_MARKER = marker
        if subfolder:
            for name in _CHILD_SUBFOLDER_GLOBALS:
                if hasattr(child, name):
                    saved[name] = getattr(child, name)
                    setattr(child, name, subfolder)
        if jxl_folder and hasattr(child, 'EXPORT_JXL_FOLDER'):
            saved['EXPORT_JXL_FOLDER'] = child.EXPORT_JXL_FOLDER
            child.EXPORT_JXL_FOLDER = jxl_folder
        # Like the marker above: set only when truthy, so the children's
        # default () stays the default for callers that never had the
        # question (transcoder/recompressor run-level answers). The
        # transcoder and recompressor simply do not have the global, and
        # hasattr there makes this a no-op — the callers pass `workflow.get(
        # 'exclude_folders')` for ALL children so the sites stay uniform.
        if exclude_folders and hasattr(child, 'EXCLUDE_FOLDERS'):
            saved['EXCLUDE_FOLDERS'] = child.EXCLUDE_FOLDERS
            child.EXCLUDE_FOLDERS = tuple(
                n.strip().lower() for n in exclude_folders.split(';') if n.strip())
        yield child
    finally:
        for name, value in saved.items():
            try:
                setattr(child, name, value)
            except Exception:
                pass


SCRIPT_DIR = Path(__file__).parent.resolve()

# Child scripts emit one machine-readable summary line per run when given
# --summary-json. The wrapper consumes it to build the end-of-manifest block
# and never prints it. Must match SUMMARY_PREFIX in the child scripts.
CHILD_SUMMARY_PREFIX = "##JXLSUM## "

# argparse exits 2 on a bad command line, and the child scripts exit 2 on a
# safety abort — the same code for "the wrapper built a command this script
# cannot accept" and "the run stopped to protect your files". Reporting the
# first as the second sent the user hunting for a collision that never existed
# (a wrapper that emitted a flag the child does not have looked, from the
# outside, exactly like a duplicate-output abort). argparse's error line is the
# one thing that tells them apart: "<prog>: error: <what>", written to stderr,
# which _stream_child merges into the output it already reads.
_CHILD_USAGE_ERROR_RE = re.compile(r"^\s*(\S+\.py): error: (.+)$")

# Where the wrapper writes its own combined log for a manifest run. Each entry
# is a separate child with its own log file, so before this there was no single
# place holding the totals or the full failure list.
# JXLPHOTO_LOG_DIR (environment variable) moves the log folders elsewhere —
# the test suite and the real-photo battery set it so their runs never
# land in Logs\ next to the scripts.
WRAPPER_LOG_DIR = (Path(os.environ["JXLPHOTO_LOG_DIR"]) if os.environ.get("JXLPHOTO_LOG_DIR")
                   else SCRIPT_DIR / "Logs") / "jxl_photo"


@dataclass
class ToolConfig:
    """Configuration dataclass for JXL tools"""
    cjxl_path: Optional[str] = None
    djxl_path: Optional[str] = None
    exiftool_path: Optional[str] = None
    magick_path: Optional[str] = None

    staging_dir: Optional[str] = None
    default_workers: int = 4
    default_quality: int = 95
    default_effort: int = 7
    # Preferred cjxl distance for TIFF -> JXL. Drives the label AND the value of
    # menu entry [2] in Step 2, so a photographer who always shoots at d=0.05
    # gets it with one keystroke instead of going through [4] Custom every time.
    # 0.1 keeps the historical "near-lossless (recommended)" entry unchanged.
    default_distance: float = 0.1
    confirm_delete: bool = True
    export_marker: str = "_EXPORT"
    # The menu's line about JXL thumbnails in Explorer (_jxl_thumbnail_tip).
    # Shown on every start, not once: someone new to JXL only meets the
    # problem later, in Explorer. Turned off in "Edit default settings".
    show_thumbnail_tip: bool = True
    # Minutes a child may print nothing before the wrapper stops it as hung
    # (_stream_child). The children print one line per completed file and,
    # since W-A of the 2026-11-03 audit, one per slow planning batch; raise it
    # for a single file or batch slower than an hour (a huge drum scan at
    # effort 10, a very slow network share). Config file only for now.
    child_idle_timeout_minutes: int = 60
    # The menu's "a new release is out" line (_update_notice): GitHub is
    # asked at most once a day, only by the interactive menu (never by
    # --run-preset or --list-presets). False turns it off; so does the
    # environment variable JXLPHOTO_NO_UPDATE_CHECK=1. Config file only.
    check_for_updates: bool = True
    update_checked_on: Optional[str] = None      # YYYY-MM-DD of the last ask
    update_latest_seen: Optional[str] = None     # the tag it answered

    last_input_dir: Optional[str] = None
    last_output_mode: Optional[str] = None
    last_workers: Optional[int] = None
    last_staging: Optional[str] = None
    last_effort: Optional[int] = None
    last_quality: Optional[int] = None
    last_distance: Optional[float] = None
    last_origin_format: Optional[str] = None
    last_dest_format: Optional[str] = None
    last_conversion_type: Optional[str] = None
    last_d50_patch: Optional[str] = None
    last_encode_tag: Optional[str] = None
    last_multipage_mode: Optional[str] = None
    last_thumbnail_mode: Optional[str] = None
    last_thumbnail_suffix: Optional[str] = None
    last_thumbnail_handling: Optional[str] = None
    last_no_reconstruct_multipage: Optional[bool] = None
    last_depth_policy: Optional[str] = None
    # Round-trip verification before a delete (TIFF -> JXL only). Its own
    # field rather than a key inside last_advanced_options, so a preset saved
    # before it existed simply reads as None/off.
    last_verify_roundtrip: Optional[bool] = None
    # Whether a delete run also covers sources whose output already exists.
    last_delete_skipped: Optional[bool] = None
    # 'path' or 'content' — how an existing output is matched to the source
    # about to replace it, in the folder-collapsing modes.
    last_provenance: Optional[str] = None
    # False only when the user explicitly turned the adopt scan off.
    last_adopt_scan: Optional[bool] = None
    # False only when the user turned the overwrite guard off (renamed
    # folders, sources never deleted). None = the child's OVERWRITE_GUARD.
    last_overwrite_guard: Optional[bool] = None
    last_advanced_options: Optional[Dict] = None
    last_use_ram: Optional[bool] = None
    last_compression: Optional[str] = None
    last_bit_depth: Optional[int] = None
    last_add_preview: Optional[bool] = None
    last_mode_config: Optional[Dict] = None
    last_icc_profile: Optional[str] = None
    last_expert_flags: Optional[str] = None
    # CSV of the last manifest run (mode 99). Only the PATH is kept, never the
    # entries: the repeat re-reads the file, so edits made in Excel between runs
    # are picked up instead of silently replaying a stale copy.
    last_manifest_path: Optional[str] = None
    # ';'-separated folder NAMES the last TIFF<->JXL run excluded from
    # discovery (--exclude-folders). Raw string, so a repeat re-splits it.
    last_exclude_folders: Optional[str] = None
    # The run's extra_options ({registry id: value}): the options the wizard
    # does not ask about, set on the Step 7 "all options" screen. One dict, not
    # a last_* field per option; a preset saved before it simply has none.
    last_extra_options: Optional[Dict] = None

    # Named snapshots of the last_* fields above ({name: {last_*: value}}), so
    # several recurring jobs can coexist instead of fighting over the single
    # "last workflow" slot. See ConfigManager.session_snapshot().
    presets: Dict[str, Dict] = field(default_factory=dict)

    dependencies_checked: bool = False
    available_features: Dict[str, bool] = field(default_factory=dict)


class ConfigManager:
    """Manages persistent configuration for jxl_tools"""

    def __init__(self):
        self.config_path = self._get_config_path()
        self.config = ToolConfig()
        self._load_config()

    def _get_config_path(self) -> Path:
        script_config = SCRIPT_DIR / ".jxl_tools_config.json"
        if script_config.exists():
            return script_config
        if platform.system() == "Windows":
            config_dir = Path(os.environ.get("USERPROFILE", Path.home()))
        else:
            config_dir = Path.home()
        return config_dir / ".jxl_tools_config.json"

    def _load_config(self) -> None:
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    valid_fields = {k: v for k, v in data.items()
                                  if k in ToolConfig.__dataclass_fields__}
                    self.config = ToolConfig(**valid_fields)
                    self._sanitise_presets()
            except Exception as e:
                print(f"Warning: Corrupted config file: {e}. Using defaults.")
                self.config = ToolConfig()

    def _sanitise_presets(self) -> None:
        """Drop presets that are not a name -> settings mapping.

        The config file is a plain JSON the user can (and does) hand-edit, and
        every consumer of `presets` assumed both levels were dicts: a list, or a
        single preset saved as a string, took down the preset menu, --list-presets
        and the main menu's counter with a traceback before anything could be
        run. Discarding the malformed entries with a warning keeps the good ones
        usable — and the file is only rewritten when the user saves, so nothing
        is lost behind their back.
        """
        raw = self.config.presets
        if raw is None:
            return
        if not isinstance(raw, dict):
            print(f"Warning: 'presets' in the config is a {type(raw).__name__}, not a "
                  f"mapping of name -> settings. Ignoring it.")
            self.config.presets = {}
            return
        clean, bad = {}, []
        for name, body in raw.items():
            if isinstance(name, str) and isinstance(body, dict):
                clean[name] = body
            else:
                bad.append(str(name))
        if bad:
            print(f"Warning: ignoring {len(bad)} malformed preset(s) in the config: "
                  f"{', '.join(sorted(bad)[:5])}"
                  + (" ..." if len(bad) > 5 else ""))
        self.config.presets = clean

    def save_config(self) -> None:
        try:
            # Atomic write (same recipe the children adopted in v2.1.1): dump
            # to a unique temp file FIRST, then swap it in with os.replace.
            # Two instances writing at once (the menu and a Task Scheduler
            # run), or a crash mid-dump, used to leave a TRUNCATED config on
            # disk — and _load_config throws every saved preset away when the
            # JSON does not parse. The final name now only ever names a
            # complete file; a killed write leaves at most a .tmp orphan that
            # nothing reads. os.replace needs the same volume, so the temp
            # lives in the config file's own folder.
            import uuid
            tmp_path = (self.config_path.parent
                        / f".{self.config_path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(asdict(self.config), f, indent=2, ensure_ascii=False)
                os.replace(str(tmp_path), str(self.config_path))
            finally:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        except IOError as e:
            print(f"Error: Failed to save config: {e}")

    _STAGING_UNSET = object()

    def save_last_session(self, input_dir: Optional[str] = None, output_mode: Optional[str] = None,
                          workers: Optional[int] = None, staging=_STAGING_UNSET,
                          effort: Optional[int] = None, quality: Optional[int] = None,
                          distance: Optional[float] = None,
                          origin_format: Optional[str] = None,
                          dest_format: Optional[str] = None,
                          conversion_type: Optional[str] = None,
                          icc_profile=_STAGING_UNSET,
                          expert_flags: Optional[str] = None,
                          d50_patch: Optional[str] = None,
                          encode_tag: Optional[str] = None,
                          multipage_mode: Optional[str] = None,
                          thumbnail_mode: Optional[str] = None,
                          thumbnail_suffix: Optional[str] = None,
                          thumbnail_handling: Optional[str] = None,
                          no_reconstruct_multipage: Optional[bool] = None,
                          depth_policy: Optional[str] = None,
                          advanced_options: Optional[Dict] = None,
                          use_ram: Optional[bool] = None,
                          compression: Optional[str] = None,
                          bit_depth: Optional[int] = None,
                          add_preview: Optional[bool] = None,
                          mode_config: Optional[Dict] = None,
                          manifest_path=_STAGING_UNSET,
                          exclude_folders: Optional[str] = _STAGING_UNSET,
                          extra_options: Optional[Dict] = None) -> None:
        if extra_options is not None:
            self.config.last_extra_options = dict(extra_options)
        if input_dir is not None:
            self.config.last_input_dir = input_dir
        if output_mode is not None:
            self.config.last_output_mode = output_mode
        if workers is not None:
            self.config.last_workers = workers
        if staging is not self._STAGING_UNSET:
            # Sentinel-based: callers that explicitly chose "system default"
            # pass None and CLEAR the saved staging; callers that didn't ask
            # simply omit the argument and the previous value is kept.
            self.config.last_staging = staging
        if effort is not None:
            self.config.last_effort = effort
        if quality is not None:
            self.config.last_quality = quality
        if distance is not None:
            self.config.last_distance = distance
        if origin_format is not None:
            self.config.last_origin_format = origin_format
        if dest_format is not None:
            self.config.last_dest_format = dest_format
        if conversion_type is not None:
            self.config.last_conversion_type = conversion_type
        if icc_profile is not self._STAGING_UNSET:
            # Sentinel-based (same as staging): callers that explicitly said
            # "no ICC conversion" pass None and CLEAR the saved profile;
            # callers that didn't ask simply omit it and the value is kept.
            self.config.last_icc_profile = icc_profile
        if expert_flags is not None:
            self.config.last_expert_flags = expert_flags
        if d50_patch is not None:
            self.config.last_d50_patch = d50_patch
        if encode_tag is not None:
            self.config.last_encode_tag = encode_tag
        if multipage_mode is not None:
            self.config.last_multipage_mode = multipage_mode
        if thumbnail_mode is not None:
            self.config.last_thumbnail_mode = thumbnail_mode
        if thumbnail_suffix is not None:
            self.config.last_thumbnail_suffix = thumbnail_suffix
        if thumbnail_handling is not None:
            self.config.last_thumbnail_handling = thumbnail_handling
        if no_reconstruct_multipage is not None:
            self.config.last_no_reconstruct_multipage = no_reconstruct_multipage
        if depth_policy is not None:
            self.config.last_depth_policy = depth_policy
        if advanced_options is not None:
            # Persist a copy of the advanced options dict for repeat workflow.
            self.config.last_advanced_options = dict(advanced_options)
        if use_ram is not None:
            self.config.last_use_ram = use_ram
        if compression is not None:
            self.config.last_compression = compression
        if bit_depth is not None:
            self.config.last_bit_depth = bit_depth
        if add_preview is not None:
            self.config.last_add_preview = add_preview
        if mode_config is not None:
            self.config.last_mode_config = dict(mode_config)
        if manifest_path is not self._STAGING_UNSET:
            # Sentinel-based (same as staging/icc): a NON-manifest run passes
            # None explicitly and CLEARS the path, so the main menu can never
            # offer to repeat a manifest that has nothing to do with the last
            # saved session.
            self.config.last_manifest_path = manifest_path
        if exclude_folders is not self._STAGING_UNSET:
            # Sentinel-based (same as staging/icc): the run-level value is
            # None BOTH when the user answered '-' (= no exclusion) and when
            # the direction never got asked the question — only the first
            # means "clear the saved value". With a plain `is not None`
            # param, a TIFF->JXL run WITH exclusions followed by one WITHOUT
            # would leave the stale list saved (bug #319(a)'s class over the
            # whole "last workflow" slot), so the callers that know the
            # answer pass None explicitly; the ones that never asked omit
            # the argument entirely.
            self.config.last_exclude_folders = exclude_folders
        self.save_config()

    def session_snapshot(self) -> Dict:
        """Every last_* field as a plain dict — the unit both 'Repeat last
        workflow' and presets are built from. Derived from the dataclass fields
        so a future last_* setting is picked up without touching this code."""
        return {name: getattr(self.config, name)
                for name in ToolConfig.__dataclass_fields__ if name.startswith('last_')}

    def save_preset(self, name: str, session: Dict) -> None:
        from datetime import datetime
        presets = dict(self.config.presets or {})
        # Schema 2 (the job) plus the v1 snapshot older copies read (JOBS).
        presets[name] = _preset_v2(dict(session))
        presets[name]['saved_at'] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.config.presets = presets
        self.save_config()

    def delete_preset(self, name: str) -> bool:
        presets = dict(self.config.presets or {})
        if name not in presets:
            return False
        del presets[name]
        self.config.presets = presets
        self.save_config()
        return True

    def update_tool_paths(self, tools: Dict[str, Optional[str]]) -> None:
        self.config.cjxl_path = tools.get('cjxl')
        self.config.djxl_path = tools.get('djxl')
        self.config.exiftool_path = tools.get('exiftool')
        self.config.magick_path = tools.get('magick')
        self.config.dependencies_checked = True
        self.save_config()

    def _check_tiff_support(self) -> bool:
        try:
            import tifffile
            import numpy
            return True
        except ImportError:
            return False

    def _check_imagecodecs(self) -> bool:
        """Check if imagecodecs is available for compressed TIFF support."""
        try:
            import imagecodecs
            return True
        except ImportError:
            return False


class DependencyChecker:
    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager
        self._deps_status = None  # cached result; force=True bypasses

    def _check_pillow(self) -> bool:
        try:
            import PIL
            return True
        except ImportError:
            return False

    def _check_rich(self) -> bool:
        try:
            import rich
            import rich.console
            import rich.table
            import rich.panel
            import rich.prompt
            return True
        except ImportError:
            return False

    def check_dependencies(self, force: bool = False, persist: bool = True) -> Dict[str, bool]:
        # Honor `force`: without it, return the cached result when available
        # (detection spawns one subprocess per tool).
        #
        # `persist=False` runs the detection but writes NOTHING to disk. It
        # exists for --list-presets, documented read-only: the tool discovery
        # still runs (the status bar stays honest) but the discovered paths and
        # the dependencies_checked stamp are not committed, so merely LISTING
        # presets never rewrites the config file.
        if not force and self._deps_status is not None:
            return self._deps_status
        tools_to_check = {
            'cjxl': ['cjxl', '--version'],
            'djxl': ['djxl', '--version'],
            'exiftool': ['exiftool', '-ver'],
            'magick': ['magick', '--version'],
        }

        detected_paths = {}
        status = {}

        for tool_name, test_cmd in tools_to_check.items():
            path = self._detect_tool(test_cmd[0])
            if path and self._test_tool_execution(path, test_cmd[1:]):
                detected_paths[tool_name] = path
                status[tool_name] = True
            else:
                detected_paths[tool_name] = None
                status[tool_name] = False

        if persist:
            self.config.update_tool_paths(detected_paths)

        status['tifffile'] = self.config._check_tiff_support()
        status['numpy'] = status['tifffile']
        status['imagecodecs'] = self.config._check_imagecodecs()
        status['pillow'] = self._check_pillow()
        status['rich'] = self._check_rich()
        status['icc_profiles'] = status.get('magick', False)

        # In-memory either way (the status display reads it); on disk only
        # when the caller did not ask for read-only.
        self.config.config.available_features = status
        if persist:
            self.config.save_config()

        self._deps_status = status
        return status

    def _detect_tool(self, cmd: str) -> Optional[str]:
        variations = [cmd]
        if platform.system() == "Windows":
            variations.extend([f"{cmd}.exe", f"{cmd}.cmd", f"{cmd}.bat"])
            if cmd == "exiftool":
                variations.extend(["exiftool(-k)", "exiftool(-k).exe",
                                   "exiftool-k", "exiftool-k.exe"])

        for variant in variations:
            path = shutil.which(variant)
            if path:
                return path

        return None

    def _test_tool_execution(self, path: str, args: List[str]) -> bool:
        try:
            result = subprocess.run([path] + args, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=10, shell=False)
            return result.returncode == 0
        except Exception:
            return False

    def format_status_line(self, status: Dict[str, bool]) -> str:
        icons = {
            'cjxl': '✓' if status.get('cjxl') else '✗',
            'djxl': '✓' if status.get('djxl') else '✗',
            'exiftool': '✓' if status.get('exiftool') else '✗',
            'magick': '✓' if status.get('magick') else '⚠',
            'tifffile': '✓' if status.get('tifffile') else '✗',
            'imagecodecs': '✓' if status.get('imagecodecs') else '✗',
            'pillow': '✓' if status.get('pillow') else '✗',
            'rich': '✓' if status.get('rich') else '✗',
        }

        # cjxl AND djxl — the label covers both, so it must not report OK when
        # only one is present. A green "cjxl/djxl" with djxl missing is exactly
        # the state where the decoder degrades multi-page groups to standalone.
        _codec_icon = '✓' if (status.get('cjxl') and status.get('djxl')) else '✗'

        parts = [
            f"[{_codec_icon}] cjxl/djxl",
            f"[{icons['exiftool']}] exiftool",
            f"[{icons['magick']}] magick",
            f"[{icons['tifffile']}] tifffile",
            f"[{icons['imagecodecs']}] imagecodecs",
            f"[{icons['pillow']}] pillow",
            f"[{icons['rich']}] rich",
        ]

        if _codec_icon == '✗':
            _missing_codecs = [n for n in ('cjxl', 'djxl') if not status.get(n)]
            parts[0] += f" ({'/'.join(_missing_codecs)} missing)"
        if not status.get('magick'):
            parts[2] += " (ICC off)"
        if not status.get('tifffile'):
            parts[3] += " (TIFF off)"
        if not status.get('imagecodecs'):
            parts[4] += " (16-bit decode & JPEG previews need: pip install imagecodecs)"
        if not status.get('pillow'):
            parts[5] += " (JPG previews off)"
        if not status.get('rich'):
            parts[6] += " (basic UI)"

        return " | ".join(parts)


# Folder names the mode-6 preview (Auto Mode / manifest generator) skips below
# an export folder in EVERY direction: the decoder's TIFF output folders (the
# encoder's mode-6 finder skips them; in the other directions they hold no
# source files anyway). Shipped defaults — used ONLY when the encoder cannot
# be imported; otherwise the encoder's own set is asked (_decoder_output_names),
# which follows the decoder's configured names (W1/E7, 2026-10-08 audit).
_DECODER_OUTPUT_NAMES = frozenset({"16b_tiff", "tiff_16bits", "converted_tiff"})


def _decoder_output_names() -> frozenset:
    """The decoder output folder names the ENCODER's finders skip — asked from
    the encoder itself, so the preview matches the child even when the
    decoder's folder settings were edited."""
    try:
        return frozenset(importlib.import_module('jxl_tiff_encoder')._DECODER_OUTPUT_FOLDERS)
    except Exception:
        return _DECODER_OUTPUT_NAMES
# Shipped defaults of jxl_recompressor._RECOMPRESSOR_OUTPUT_FOLDERS + its
# EXPORT_JXL_FOLDER — used ONLY if the child cannot be imported.
_RECOMPRESSOR_OUTPUT_FALLBACK = frozenset(
    {"recompressed_jxl", "jxl_recompressed", "jxl_small", "16b_jxl_small"})


def _mode6_preview_skips(origin: str, dest: str, below_lower: List[str]) -> bool:
    """Does the CHILD's mode-6 finder skip a file whose folder parts BELOW the
    export folder are `below_lower`? The preview count and the generated
    manifest must match what the child will process (the #269/#297 class).

    The recompressor's own output folders are skipped ONLY for JXL -> JXL:
    that child is the only one whose finders skip them — the decoder decodes a
    JXL inside JXL_small/ like any other, so skipping it there under-counted
    the run and could drop an export folder from a generated manifest (round
    43). Asked from the recompressor itself (`_is_own_output_path`): it knows
    its configured EXPORT_JXL_FOLDER and the _JXL_small suffix rule, so a
    folder name edited at the top of that script is honored here too.
    """
    _dec_names = _decoder_output_names()
    if any(p in _dec_names for p in below_lower):
        return True
    if (origin, dest) != ('jxl', 'jxl'):
        return False
    try:
        rec = importlib.import_module('jxl_recompressor')
        return bool(rec._is_own_output_path(below_lower))
    except Exception:
        own = _export_folder_name('jxl', 'jxl').lower()
        return any(p in _RECOMPRESSOR_OUTPUT_FALLBACK or p == own
                   or p.endswith("_jxl_small") for p in below_lower)


class FolderAnalyzer:
    """Analyzes folder structure to recommend best mode."""

    def __init__(self, root_path: Path, origin: str, dest: str,
                 export_marker: str = ToolConfig.export_marker):
        self.root = root_path
        self.origin = origin
        self.dest = dest
        self.export_marker = export_marker

    def analyze(self) -> Dict[str, Any]:
        """Scan folder and return analysis results."""
        result = {
            'folder_count': 0,
            'total_files': 0,
            'has_export_marker': False,
            'export_marker_paths': [],
            'has_subfolders': False,
            'subfolders': [],
            'has_flat_structure': False,
            'has_recursive_structure': False,
            'file_distribution': {},
            'recommended_mode': None,
            'confidence': 'low',
            'reasoning': [],
        }

        if not self.root.exists() or not self.root.is_dir():
            return result

        # Collect items by iterating (not materializing the whole tree with
        # list(rglob) — large libraries would blow up RAM and scan time).
        origin_exts = self._get_extensions(self.origin)
        marker_lower = self.export_marker.lower()

        all_folders = []
        origin_files = []
        file_distribution = {}

        for p in self.root.rglob('*'):
            try:
                if p.is_dir():
                    if self._is_hidden(p):
                        continue
                    all_folders.append(p)
                elif p.is_file():
                    if self._is_hidden(p):
                        continue
                    ext = p.suffix.lower()
                    if ext in origin_exts:
                        origin_files.append(p)
                        parent = str(p.parent.relative_to(self.root))
                        file_distribution[parent] = file_distribution.get(parent, 0) + 1
            except OSError:
                continue  # unreadable entry (permissions, race with other tools)

        result['total_files'] = len(origin_files)
        result['folder_count'] = len(all_folders)

        # Check for export markers (case-insensitive, prefix or suffix only)
        marker_lower = self.export_marker.lower()
        for folder in all_folders:
            if _marker_matches(folder.name.lower(), marker_lower):
                result['has_export_marker'] = True
                result['export_marker_paths'].append(str(folder))

        # Analyze structure
        immediate_subfolders = [d for d in self.root.iterdir() if d.is_dir() and not self._is_hidden(d)]

        if len(immediate_subfolders) > 0:
            result['has_subfolders'] = True
            # Keep the REAL count for mode logic/messages; the list itself is
            # only a display sample and is truncated to 5.
            result['subfolder_count'] = len(immediate_subfolders)
            result['subfolders'] = [str(d) for d in immediate_subfolders[:5]]
            # ALL immediate subfolder names, for decisions that must not be made
            # on a display sample. The mode-4 heuristic used to read
            # subfolders[:3], so a tree whose only '*_TIFF' folder sorted past
            # position 5 never got the rename mode recommended.
            result['subfolder_names'] = [d.name for d in immediate_subfolders]

            # Check if it's a flat structure (origin files in root)
            if any(f.parent == self.root for f in origin_files):
                result['has_flat_structure'] = True

            # Check if subfolders contain origin files
            subfolder_with_files = set(f.parent for f in origin_files if f.parent != self.root)
            if len(subfolder_with_files) > 1:
                result['has_recursive_structure'] = True

        # Count files per folder (accumulated during the scan above)
        result['file_distribution'] = file_distribution

        # Determine recommendation
        self._recommend(result, origin_files)

        return result

    def _is_hidden(self, path: Path) -> bool:
        """Check if path is hidden (starts with dot or has hidden attribute on Windows)."""
        if path.name.startswith('.'):
            return True
        if platform.system() == "Windows":
            try:
                import ctypes
                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
                return attrs != -1 and (attrs & 2)  # FILE_ATTRIBUTE_HIDDEN
            except Exception:
                return False
        return False

    def _get_extensions(self, fmt: str) -> set:
        """Get file extensions for a format."""
        mapping = {
            'jpeg': {'.jpg', '.jpeg', '.jfif', '.jpe'},
            'tiff': {'.tif', '.tiff'},
            'jxl': {'.jxl'},
            'png': {'.png'},
        }
        return mapping.get(fmt.lower(), set())

    def _recommend(self, result: Dict, origin_files: List[Path]):
        """Determine recommended mode based on analysis."""
        reasoning = []
        confidence = 'low'

        # Mode 6 or 7: export marker detected
        if result['has_export_marker']:
            export_paths = result['export_marker_paths']
            reasoning.append(f"Found {len(export_paths)} folder(s) with '{self.export_marker}' in name")

            # Check if there's a source-format subfolder inside _EXPORT (mode 7).
            # Only subfolders that contain ORIGIN files count — the toolkit's
            # own output folders (16B_JXL etc.) must not trigger this, or every
            # run after the first would recommend mode 7 for the wrong reason.
            # Mode 7 is only recommended when a SINGLE subfolder name holds the
            # origin files; with several names the choice would be arbitrary
            # (and the preview would lie) — fall back to mode 6.
            origin_sub_names = set()
            origin_exts = self._get_extensions(self.origin)
            # Every export folder, not the first three (W10 of the 2026-10-08
            # audit): mode 7 takes ONE subfolder name for the whole run, so a
            # 4th export folder using another name would silently lose its
            # files to the recommendation. Stops at the second name — the
            # answer is mode 6 from there on.
            for export_path in export_paths:
                if len(origin_sub_names) > 1:
                    break
                export_dir = Path(export_path)
                try:
                    subdirs = sorted((d for d in export_dir.iterdir() if d.is_dir()),
                                     key=lambda d: d.name.lower())
                except OSError:
                    continue  # unreadable/removed mid-scan: skip, don't kill analysis
                for subdir in subdirs:
                    try:
                        if any(f.is_file() and f.suffix.lower() in origin_exts
                               for f in subdir.rglob('*')):
                            origin_sub_names.add(subdir.name)
                    except OSError:
                        continue

            if len(origin_sub_names) == 1:
                result['recommended_mode'] = 7
                confidence = 'high'
                reasoning.append(f"Origin files live in subfolder '{next(iter(origin_sub_names))}' of the export folder — Mode 7 recommended (specific subfolder)")
            elif len(origin_sub_names) > 1:
                result['recommended_mode'] = 6
                confidence = 'medium'
                reasoning.append(f"Origin files spread across {len(origin_sub_names)} subfolders ({', '.join(sorted(origin_sub_names))}) — Mode 6 recommended (all export files; pick a subfolder manually for mode 7)")
            else:
                result['recommended_mode'] = 6
                confidence = 'high'
                reasoning.append("Mode 6 recommended — processes all files inside export folders")

        # Mode 4: folder rename when source type is in folder name.
        # Checked before the generic recursive/flat modes — a folder tree
        # named after the origin format (e.g. "..._TIFF") is the strongest
        # signal for a rename workflow. (Previously nearly unreachable.)
        elif result['has_subfolders'] and any(
                self.origin.lower() in n.lower()
                for n in result.get('subfolder_names')
                or [Path(p).name for p in result['subfolders']]):
            result['recommended_mode'] = 4
            confidence = 'medium'
            reasoning.append(f"Folder names contain '{self.origin}' — Mode 4 (rename/suffix) recommended")

        # Mode 3: recursive subfolders with files
        elif result['has_recursive_structure'] and result['total_files'] > 10:
            result['recommended_mode'] = 3
            confidence = 'high'
            reasoning.append(f"Recursive structure detected — {len(result['file_distribution'])} subfolders with files")
            reasoning.append(f"Mode 3 recommended — creates "
                             f"'{_dest_folder_names(self.origin, self.dest)[1]}' "
                             f"subfolder in each location")

        # Mode 2: flat output folder (only when there really are multiple
        # subfolders — a single subfolder is better served by mode 1)
        elif result['has_subfolders'] and result.get('subfolder_count', len(result['subfolders'])) > 1 and result['total_files'] > 5:
            result['recommended_mode'] = 2
            confidence = 'medium'
            reasoning.append(f"Multiple subfolders found ({result.get('subfolder_count', len(result['subfolders']))}) with many files")
            reasoning.append("Mode 2 recommended — merges all to single output folder")

        # Mode 1: single subfolder
        elif result['has_subfolders'] and result.get('subfolder_count', len(result['subfolders'])) == 1:
            result['recommended_mode'] = 1
            confidence = 'medium'
            reasoning.append("Single subfolder structure detected")
            reasoning.append(f"Mode 1 recommended — creates "
                             f"'{_dest_folder_names(self.origin, self.dest)[0]}' subfolder")

        # Mode 0: flat (files in root)
        elif result['has_flat_structure'] and result['total_files'] > 0:
            result['recommended_mode'] = 0
            confidence = 'high'
            reasoning.append("Files found in root folder — in-place mode works well")
            reasoning.append("Mode 0 recommended — files stay side by side")

        else:
            result['recommended_mode'] = 0
            confidence = 'low'
            reasoning.append("No clear structure pattern — defaulting to Mode 0 (in-place)")
            reasoning.append("Use manual mode selection if this doesn't fit your workflow")

        result['confidence'] = confidence
        result['reasoning'] = reasoning

    def format_report(self, analysis: Dict) -> str:
        """Format analysis as human-readable report."""
        lines = []

        lines.append(f"\n{'='*60}")
        lines.append("FOLDER ANALYSIS")
        lines.append(f"{'='*60}")
        lines.append(f"Total {self.origin.upper()} files: {analysis['total_files']}")
        lines.append(f"Total folders scanned: {analysis['folder_count']}")
        lines.append(f"Export folder found: {'Yes' if analysis['has_export_marker'] else 'No'}")

        if analysis['has_export_marker']:
            lines.append(f"  Locations: {', '.join(analysis['export_marker_paths'][:2])}")
            if len(analysis['export_marker_paths']) > 2:
                lines.append(f"  ... and {len(analysis['export_marker_paths'])-2} more")

        if analysis['has_subfolders']:
            # The REAL count, not len() of the 5-item display sample analyze()
            # stores in 'subfolders' — a tree with 40 shoots reported
            # "Subfolders: 5 ... and 2 more". Same class as #248: _recommend was
            # taught to use subfolder_count and this report was missed.
            _sub_total = analysis.get('subfolder_count', len(analysis['subfolders']))
            lines.append(f"Subfolders: {_sub_total}")
            for sub in analysis['subfolders'][:3]:
                lines.append(f"  - {sub}")
            if _sub_total > 3:
                lines.append(f"  ... and {_sub_total - 3} more")

        lines.append(f"\nFile distribution: {len(analysis['file_distribution'])} folder(s)")
        for folder, count in list(analysis['file_distribution'].items())[:3]:
            lines.append(f"  {folder}: {count} file(s)")
        if len(analysis['file_distribution']) > 3:
            lines.append(f"  ... and {len(analysis['file_distribution'])-3} more folder(s)")

        # Real folder names per direction — the literal "{dest}" placeholders
        # that used to sit here were never formatted, so the report advertised
        # folders no script creates (same class as bugs #161/#169, which fixed
        # the wizard tables and missed the analyzer).
        _sub1, _sub3 = _dest_folder_names(self.origin, self.dest)
        mode_names = {
            0: "In-place (same folder)",
            1: f"Subfolder ({_sub1})",
            2: "Flat (all to one folder)",
            3: f"Recursive subfolders ({_sub3})",
            4: "Folder rename (suffix swap)",
            5: "Sibling folder",
            6: f"Marker export (full)",
            7: f"Marker export (specific subfolder)",
        }

        lines.append(f"\n{'='*60}")
        if analysis['recommended_mode'] is not None:
            rec_mode = analysis['recommended_mode']
            rec_name = mode_names.get(rec_mode, f"Mode {rec_mode}")
            confidence = analysis['confidence']
            confidence_icon = {"high": "✓✓", "medium": "✓", "low": "?"}[confidence]

            lines.append(f"{confidence_icon} RECOMMENDED: Mode {rec_mode} — {rec_name}")
            lines.append(f"   Confidence: {confidence}")

            if analysis['reasoning']:
                lines.append(f"   Reasoning:")
                for r in analysis['reasoning']:
                    lines.append(f"     - {r}")

        lines.append(f"{'='*60}\n")

        return "\n".join(lines)

    @staticmethod
    def _outermost_with_counts(eligible: List[Tuple[str, int]]) -> List[Tuple[str, int]]:
        """Keep only the OUTERMOST folders, folding the rest into their parent.

        Modes 3/4/5 make the child walk the whole tree under each Source
        (find_tiffs_recursive and friends), so an entry nested inside another
        entry hands the SAME files to a second child process: identical outputs
        written twice, decided by sync-mtime luck. The wrapper's own overlap
        guard then refuses the manifest its own Auto Mode had just generated.

        The dropped folders' files are still converted — by the outer entry that
        recurses into them — so their counts are folded in, keeping the preview
        honest instead of under-reporting.

        `eligible` is [(relative_folder, count)]; '.' means the scan root. Only
        folders present in `eligible` count as ancestors: modes 4/5 exclude the
        root on purpose, and it must not silently swallow every entry there.
        """
        def _key(folder: str) -> tuple:
            if folder in ('.', ''):
                return ()
            return tuple(p.lower() for p in Path(folder).parts)

        keys = {_key(f): f for f, _c in eligible}
        # Shallowest ancestor wins: range() walks outwards-in, so a chain
        # a/ -> a/b/ -> a/b/c/ collapses onto a/ rather than onto a/b/.
        owner = {}
        for folder, _count in eligible:
            k = _key(folder)
            owner[folder] = next((keys[k[:i]] for i in range(len(k)) if k[:i] in keys),
                                 folder)
        totals: Dict[str, int] = {}
        for folder, count in eligible:
            totals[owner[folder]] = totals.get(owner[folder], 0) + count
        return [(f, totals[f]) for f, _c in eligible if owner[f] == f]

    def compute_folder_mappings(self, analysis: Dict, mode: int) -> List[Tuple[str, str, int]]:
        """Compute source -> destination folder mappings for a given mode.
        Returns list of (source_path, dest_path, file_count).
        """
        mappings = []
        origin_exts = self._get_extensions(self.origin)

        if mode == 6:
            # Process all files inside export folders, skipping exactly the
            # tool output folders the CHILD's mode-6 finder skips for this
            # direction (_mode6_preview_skips) — the preview count must match
            # what the child will do.
            for export_path in analysis['export_marker_paths']:
                export_dir = Path(export_path)
                origin_files = [
                    f for f in export_dir.rglob('*')
                    if f.is_file() and f.suffix.lower() in origin_exts
                    and not _mode6_preview_skips(
                        self.origin, self.dest,
                        [part.lower() for part in f.relative_to(export_dir).parts[:-1]])
                ]
                if origin_files:
                    mappings.append((str(export_dir), str(export_dir), len(origin_files)))

        elif mode == 7:
            # Export folder / specific subfolder (auto-detect or default to JXL)
            for export_path in analysis['export_marker_paths']:
                export_dir = Path(export_path)
                # Try common subfolder names, or auto-detect any subfolder with origin files
                subfolder_name = "JXL"
                jxl_subfolder = export_dir / subfolder_name
                if not jxl_subfolder.exists() or not any(
                    f.is_file() and f.suffix.lower() in origin_exts
                    for f in jxl_subfolder.rglob('*')
                ):
                    # Auto-detect first subfolder containing origin files
                    # (sorted for deterministic previews/manifests)
                    try:
                        candidates = sorted((d for d in export_dir.iterdir() if d.is_dir()),
                                            key=lambda d: d.name.lower())
                    except OSError:
                        candidates = []
                    for subdir in candidates:
                        if any(
                            f.is_file() and f.suffix.lower() in origin_exts
                            for f in subdir.rglob('*')
                        ):
                            jxl_subfolder = subdir
                            break
                    else:
                        jxl_subfolder = export_dir / subfolder_name  # fallback
                origin_files = [
                    f for f in jxl_subfolder.rglob('*')
                    if f.is_file() and f.suffix.lower() in origin_exts
                ] if jxl_subfolder.exists() else []
                if origin_files:
                    mappings.append((str(jxl_subfolder), str(jxl_subfolder), len(origin_files)))

        elif mode == 0:
            # In-place: each folder that has files
            for folder, count in analysis['file_distribution'].items():
                if count > 0:
                    src = str(self.root / folder) if folder != '.' else str(self.root)
                    mappings.append((src, src, count))

        elif mode == 1:
            # Subfolder: real per-direction name inside each source folder
            for folder, count in analysis['file_distribution'].items():
                if count > 0:
                    src = str(self.root / folder) if folder != '.' else str(self.root)
                    dest = str(Path(src) / _dest_folder_names(self.origin, self.dest)[0])
                    mappings.append((src, dest, count))

        elif mode == 2:
            # Flat: all to one output folder (matches the Step 5 default)
            total = sum(analysis['file_distribution'].values())
            if total > 0:
                out_dir = str(self.root.parent / "output")
                mappings.append((str(self.root), out_dir, total))

        elif mode == 3:
            # Recursive: each folder gets its own real subfolder name.
            # The ROOT ('.') is included, like modes 0 and 1: the child DOES
            # convert files sitting directly in the input root (output goes to
            # <root>/JXL_16bits/), so excluding it here silently dropped them
            # from manifest runs while a direct run converted them.
            # Modes 4/5 below still exclude the root on purpose — there the
            # output would land OUTSIDE the selected input tree.
            #
            # Nested folders are dropped: the child RECURSES from each Source,
            # so an entry inside another entry would re-run the same files as a
            # second process (see _outermost_with_counts).
            for folder, count in self._outermost_with_counts(
                    [(f, c) for f, c in analysis['file_distribution'].items() if c > 0]):
                src = str(self.root / folder) if folder != '.' else str(self.root)
                dest = str(Path(src) / _dest_folder_names(self.origin, self.dest)[1])
                mappings.append((src, dest, count))

        elif mode in [4, 5]:
            # Mode 4: rename (replace origin with dest in folder name)
            # Mode 5: sibling folder next to each source folder
            # Mode 5's SIBLING names differ from the mode-1/3 subfolder names
            # (the transcoder's jxl->jpeg sibling is JPEG_recovered, not
            # recovered_jpeg; jpeg->jxl is JXL_jpeg, not converted_jxl), so the
            # mode-3 helper cannot be reused here. Only the jxl->jxl case was
            # wrong: the recompressor writes JXL_recompressed.
            if self.origin == 'jxl' and self.dest == 'jxl':
                sibling_name = _dest_folder_names(self.origin, self.dest)[1]
            else:
                sibling_name = {
                    'tiff': 'TIFF_16bits',
                    'jpeg': 'JPEG_recovered',
                    'png': 'JPEG_recovered',
                }.get(self.dest, 'JXL_16bits' if self.origin == 'tiff' else 'JXL_jpeg')
            # Same recursion rule as mode 3, but the root is not an eligible
            # entry here, so it must not be treated as an ancestor either —
            # otherwise every entry would collapse onto a folder that is never
            # emitted and the manifest would come out empty.
            for folder, count in self._outermost_with_counts(
                    [(f, c) for f, c in analysis['file_distribution'].items()
                     if c > 0 and f != '.']):
                src = str(self.root / folder)
                if mode == 4:
                    # Replace origin in the FOLDER NAME (not the relative
                    # path — the scripts operate on parent.name) with the
                    # script's actual destination suffix.
                    if self.origin == 'jxl' and self.dest == 'jxl':
                        _dest_suffix = 'JXL_small'   # jxl_recompressor.JXL_SUFFIX_REPLACE
                    elif self.dest in ('jpeg', 'png'):
                        _dest_suffix = 'JPEG_recovered'
                    else:
                        _dest_suffix = self.dest.upper()
                    new_name = _replace_suffix_token(Path(folder).name, self.origin, _dest_suffix)
                    if new_name == Path(folder).name:
                        new_name = Path(folder).name + "_" + _dest_suffix
                    dest = str(self.root / str(Path(folder).parent / new_name))
                else:
                    dest = str(Path(src).parent / sibling_name)
                mappings.append((src, dest, count))

        return mappings

    def generate_manifest(self, analysis: Dict, mode: int) -> List[Tuple[str, str, int]]:
        """Generate manifest entries based on mode.

        Returns list of (source, destination, file_count, mode).
        """
        mappings = []
        origin_exts = self._get_extensions(self.origin)

        if mode == 6:
            # For mode 6/7, generate one entry per export folder, skipping the
            # tool output folders the child's mode-6 finder skips for this
            # direction (_mode6_preview_skips).
            for export_path in analysis['export_marker_paths']:
                export_dir = Path(export_path)
                origin_files = [
                    f for f in export_dir.rglob('*')
                    if f.is_file() and f.suffix.lower() in origin_exts
                    and not _mode6_preview_skips(
                        self.origin, self.dest,
                        [part.lower() for part in f.relative_to(export_dir).parts[:-1]])
                ]
                if origin_files:
                    mappings.append((str(export_dir), str(export_dir), len(origin_files), 6))
        elif mode == 7:
            # Mode 7: export / subfolder (auto-detect or default to JXL)
            for export_path in analysis['export_marker_paths']:
                export_dir = Path(export_path)
                subfolder_name = "JXL"
                jxl_subfolder = export_dir / subfolder_name
                if not jxl_subfolder.exists() or not any(
                    f.is_file() and f.suffix.lower() in origin_exts
                    for f in jxl_subfolder.rglob('*')
                ):
                    # Auto-detect first subfolder containing origin files
                    # (sorted for deterministic previews/manifests)
                    try:
                        candidates = sorted((d for d in export_dir.iterdir() if d.is_dir()),
                                            key=lambda d: d.name.lower())
                    except OSError:
                        candidates = []
                    for subdir in candidates:
                        if any(
                            f.is_file() and f.suffix.lower() in origin_exts
                            for f in subdir.rglob('*')
                        ):
                            jxl_subfolder = subdir
                            break
                    else:
                        jxl_subfolder = export_dir / subfolder_name  # fallback
                origin_files = [
                    f for f in jxl_subfolder.rglob('*')
                    if f.is_file() and f.suffix.lower() in origin_exts
                ] if jxl_subfolder.exists() else []
                if origin_files:
                    mappings.append((str(jxl_subfolder), str(jxl_subfolder), len(origin_files), 7))
        else:
            # For other modes, use compute_folder_mappings
            mappings = [
                (src, dst, count, mode)
                for src, dst, count in self.compute_folder_mappings(analysis, mode)
            ]

        return mappings

    def detect_mode_for_entry(self, source: str, dest: str, original_mode: int = 0) -> int:
        """Auto-detect mode from source/dest pair.

        - Preserves explicitly generated manifest modes.
        - If source == dest -> mode 0 (in-place)
        - If dest is a subfolder of source and contains export-like structure -> mode 7
        - Otherwise -> mode 0 (default)
        """
        src_path = Path(source)
        dst_path = Path(dest)

        # Preserve the mode stored in the manifest. The manifest was generated with
        # a specific mode, so re-detecting would lose modes 1/2/3/4/5 and could
        # turn them into 0 or 7 incorrectly. original_mode=None means "no Mode
        # cell" (legacy hand-made manifest) — only THEN do marker detection.
        if original_mode is not None:
            return original_mode

        if src_path == dst_path:
            return 0

        # Check if dest is a subfolder of source with export-like name.
        # The marker must be a path PART of the RELATIVE path (not the full
        # absolute path — living under F:\_EXPORT must not promote a manifest
        # entry to mode 6 by itself).
        try:
            rel = dst_path.relative_to(src_path)
            marker_lower = self.export_marker.lower()
            marker_in_dest = any(
                _marker_matches(p.lower(), marker_lower)
                for p in rel.parts
            )
            if marker_in_dest:
                # Marker IS the destination (src/_EXPORT): whole-marker mode 6.
                # Marker + subfolder below it (src/_EXPORT/sub): mode 7.
                # (These were inverted before.)
                if src_path == dst_path.parent:
                    return 6
                return 7
        except ValueError:
            pass

        return 0


def _dest_folder_names(origin: str, dest: str) -> tuple:
    """Real output subfolder names created by the backend scripts, by direction.

    Returns (mode1_subfolder, mode3_subfolder), READ from the child that
    creates them (CONVERTED_*_FOLDER / *_FOLDER_NAME at the top of that
    script) — W1 of the 2026-10-08 audit: literal copies of the defaults made
    the "About to delete originals" panel and the previews name the shipped
    folder even after the user edited the script. The literals below are only
    the fallbacks for a child that cannot be imported.
    """
    if origin == 'jxl' and dest == 'jxl':
        # jxl_recompressor: CONVERTED_JXL_FOLDER / JXL_FOLDER_NAME — NOT the
        # transcoder's converted_jxl. Wrong names here made the previews and
        # the "About to delete originals" panel point at folders the run
        # never created.
        return (_child_setting('jxl_recompressor', 'CONVERTED_JXL_FOLDER',
                               'recompressed_jxl'),
                _child_setting('jxl_recompressor', 'JXL_FOLDER_NAME',
                               'JXL_recompressed'))
    if dest == 'tiff':
        return (_child_setting('jxl_tiff_decoder', 'CONVERTED_TIFF_FOLDER',
                               'converted_tiff'),
                _child_setting('jxl_tiff_decoder', 'TIFF_FOLDER_NAME', 'TIFF_16bits'))
    if dest == 'jxl':
        if origin == 'tiff':
            return (_child_setting('jxl_tiff_encoder', 'CONVERTED_JXL_FOLDER',
                                   'converted_jxl'),
                    _child_setting('jxl_tiff_encoder', 'JXL_FOLDER_NAME', 'JXL_16bits'))
        _tj = _child_setting('jxl_jpeg_transcoder', 'CONVERTED_JXL_FOLDER',
                             'converted_jxl')
        return (_tj, _tj)
    # jpeg / png outputs come from the transcoder's decode path
    _rj = _child_setting('jxl_jpeg_transcoder', 'RECOVERED_JPEG_FOLDER',
                         'recovered_jpeg')
    return (_rj, _rj)


def _row_effective_options(workflow: Dict, advanced: Dict, row_opts: Optional[Dict],
                           origin: str, dest: str) -> Tuple[Dict, Dict]:
    """(workflow_row, advanced_row): shallow copies with this row's manifest
    columns applied. A key PRESENT in row_opts overrides (None/''/'none'
    removes the option); an absent key keeps the wizard's value. The export_*
    keys are the exception: they only override when they have a value (they
    never remove). `exclude_folders` is a hybrid: '' was mapped to an ABSENT
    key by _parse_manifest_row_options (keeps the run's value), '-'/'none'
    lands as None (removes), a value is the row's own raw string."""
    wf = dict(workflow)
    adv = dict(advanced)
    if not row_opts:
        return wf, adv
    if 'exclude_folders' in row_opts:
        wf['exclude_folders'] = row_opts['exclude_folders'] or None
    if 'output_icc' in row_opts:
        val = row_opts['output_icc']
        if origin == 'jxl' and dest == 'jxl':
            if val:
                adv['output_icc'] = val
            else:
                adv.pop('output_icc', None)
        elif origin == 'jxl' and dest in ('jpeg', 'png'):
            # The transcoder receives the ICC via workflow['icc_profile'];
            # see the transcoder branch of _build_manifest_entry_cmd. The
            # value is mirrored into advanced['output_icc'] as well so the
            # derivative guards (_has_derivative_options: a converted row
            # never deletes its source) see it.
            wf['icc_profile'] = val or None
            if val:
                adv['output_icc'] = val
            else:
                adv.pop('output_icc', None)
    if 'resize_mode' in row_opts:
        if row_opts['resize_mode']:
            adv['resize_mode'] = row_opts['resize_mode']
            adv['resize_value'] = row_opts['resize_value']
            if row_opts.get('allow_upscale'):
                adv['allow_upscale'] = True
            else:
                adv.pop('allow_upscale', None)
        else:
            for _k in ('resize_mode', 'resize_value', 'allow_upscale'):
                adv.pop(_k, None)
    if 'sharpen' in row_opts:
        if row_opts['sharpen'] and row_opts['sharpen'] != 'none':
            adv['sharpen'] = row_opts['sharpen']
        else:
            adv.pop('sharpen', None)
    if 'rename_from' in row_opts or 'rename_to' in row_opts:
        rf = row_opts.get('rename_from') or ''
        rt = row_opts.get('rename_to') or ''
        if rf:
            adv['rename_from'] = rf
            adv['rename_to'] = rt
        else:
            adv.pop('rename_from', None)
            adv.pop('rename_to', None)
    _export_keys = ('export_marker', 'export_subfolder', 'export_jxl_folder')
    if any(row_opts.get(k) for k in _export_keys):
        # COPY: wf is a shallow copy, so wf['mode_config'] is the run's own
        # dict — mutating it would leak this row's values into every row.
        mc = dict(wf.get('mode_config') or {})
        for k in _export_keys:
            if row_opts.get(k):
                mc[k] = row_opts[k]
        wf['mode_config'] = mc
    return wf, adv


def _row_options_summary(row_opts: Optional[Dict]) -> str:
    """One short 'sRGB · long:2048 · screen · ProPhoto→sRGB' label for the
    manifest confirmation panel — '' when the row applies nothing."""
    if not row_opts:
        return ""
    parts = []
    if row_opts.get('output_icc'):
        parts.append(str(row_opts['output_icc']))
    if row_opts.get('resize_mode'):
        _unit = "%" if row_opts['resize_mode'] == "percent" else ""
        _v = row_opts.get('resize_value')
        _lbl = f"{_v}{_unit}" if _unit else f"{row_opts['resize_mode']}:{_v}"
        parts.append(_lbl + ("+up" if row_opts.get('allow_upscale') else ""))
    if row_opts.get('sharpen') and row_opts['sharpen'] != 'none':
        parts.append(row_opts['sharpen'])
    if row_opts.get('rename_from'):
        parts.append(f"{row_opts['rename_from']}→{row_opts.get('rename_to') or ''}")
    if row_opts.get('export_marker'):
        parts.append(f"marker:{row_opts['export_marker']}")
    if row_opts.get('export_subfolder'):
        parts.append(f"sub:{row_opts['export_subfolder']}")
    if row_opts.get('export_jxl_folder'):
        parts.append(f"out:{row_opts['export_jxl_folder']}")
    if row_opts.get('exclude_folders'):
        parts.append(f"excl:{row_opts['exclude_folders']}")
    return " · ".join(parts)


def _drop_derivative_options(advanced: Dict) -> None:
    """Forget the derivative answers (--output-icc, the rename the wizard only
    asks alongside it, and the resize/sharpening recipe). Used whenever Step 6
    does not offer or does not take the derivative, so an answer from an
    earlier pass through the step cannot reach the command."""
    for _k in ('output_icc', 'rename_from', 'rename_to',
               'resize_mode', 'resize_value', 'allow_upscale', 'sharpen'):
        advanced.pop(_k, None)


def _step6_delete_on(workflow: Dict) -> bool:
    """Step 6: is a delete already requested (Step 4's [D], or expert flags)?
    A derivative never deletes its source, so Step 6 does not offer one then
    — the run would only be refused later, after the Step 7 YES (W7 of the
    2026-10-08 audit)."""
    return bool(workflow.get('delete_source')
                or (workflow.get('advanced_options') or {}).get('delete_source')
                or _flags_request_delete(workflow.get('expert_flags')))


_NO_DERIVATIVE_WITH_DELETE = ("A derivative (colour conversion, resize, sharpening) never "
                              "deletes its source, so it is not offered while [D] "
                              "(delete originals) is on.")


def _has_derivative_options(advanced: Dict) -> bool:
    """True when the options produce a DERIVATIVE: colour conversion, resize
    or sharpening. These never delete, never run in place and carry a
    jxlphoto-derived recipe marker."""
    return bool(advanced.get('output_icc') or advanced.get('resize_mode')
                or (advanced.get('sharpen') or 'none') != 'none')


def _lossy_decode_never_deletes(workflow: Dict) -> Optional[str]:
    """Why this JXL -> JPEG/PNG run cannot delete its JXLs, or None (T-B of
    the 2026-11-03 audit, the owner's option 1; the transcoder refuses the
    same pair with exit 2 and keeps such sources per file). A JPEG decoded
    from a JXL is lossy and an 8-bit PNG may be a degraded copy: neither is
    a replacement for the master. The bit-exact recovery of a JPEG archive
    and a 16-bit PNG still may delete."""
    if workflow.get('origin_format') != 'jxl':
        return None
    conv = workflow.get('conversion_type', '')
    if conv == 'jxl_to_jpeg_force':
        return ("a JPEG decoded from a JXL is lossy, not a copy of it — only the "
                "bit-exact recovery of a JPEG archive deletes its JXL")
    if conv == 'jxl_to_png' and str(_bit_depth_or_default(workflow, 'png')) != '16':
        return "an 8-bit PNG may be a degraded copy of the JXL — use 16 bits to delete"
    return None


def _append_derivative_flags(cmd: List, advanced: Dict) -> None:
    """Append the resize/sharpening flags the two sources read.

    Emitted only for the transcoder's decode direction and the recompressor
    (their callers); the encoder/decoder have no such flags."""
    _mode = advanced.get('resize_mode')
    _value = advanced.get('resize_value')
    if _mode == 'long' and _value:
        cmd.extend(['--resize-long', str(_value)])
    elif _mode == 'short' and _value:
        cmd.extend(['--resize-short', str(_value)])
    elif _mode == 'percent' and _value:
        cmd.extend(['--resize-percent', str(_value)])
    if advanced.get('allow_upscale'):
        cmd.append('--allow-upscale')
    if (advanced.get('sharpen') or 'none') != 'none':
        cmd.extend(['--sharpen', advanced['sharpen']])


def _resize_display(advanced: Dict) -> str:
    """One-line description of the resize recipe for the Step 7 summary."""
    _mode = advanced.get('resize_mode')
    if not _mode:
        return "none"
    _label = {"long": "long edge", "short": "short edge",
              "percent": "percent"}.get(_mode, _mode)
    _value = advanced.get('resize_value')
    _unit = "%" if _mode == "percent" else " px"
    return (f"{_label} {_value}{_unit}"
            + (" (upscale allowed)" if advanced.get('allow_upscale') else ""))


def _shaping_text(adv: Dict) -> str:
    """'long 2048, screen' — the resize/sharpening recipe in a few words."""
    bits = []
    if adv.get('resize_mode'):
        v = adv.get('resize_value')
        bits.append(f"{adv['resize_mode']} {v:g}" if isinstance(v, float) else
                    f"{adv['resize_mode']} {v}")
        if adv.get('allow_upscale'):
            bits.append("upscale")
    if (adv.get('sharpen') or 'none') != 'none':
        bits.append(f"sharpen {adv['sharpen']}")
    return ", ".join(bits) if bits else "none"


def _ask_output_shaping(workflow: Dict, ui: "Optional[UI]" = None) -> None:
    """The resize/sharpening questions ('review.resize', 'review.resize_value',
    'review.upscale', 'review.sharpen'), through `ui` (PlainUI when none is
    given: the same input() prompts as before). Every answer starts from
    "none": the questions set the whole recipe again.

    Fills advanced_options['resize_mode'/'resize_value'/'allow_upscale'/
    'sharpen']; "none" CLEARS all of them, so an answer from an earlier pass
    can never ride along into the command."""
    if ui is None:
        ui = RichUI() if (RICH_AVAILABLE and console) else PlainUI()
    _adv = workflow.setdefault('advanced_options', {})
    _mode = ui.choose("review.resize", "Resize",
                      [("none", "no resize"), ("long", "long edge in pixels"),
                       ("short", "short edge in pixels"), ("percent", "scale in percent")],
                      "none")
    if _mode == "none":
        for _k in ('resize_mode', 'resize_value', 'allow_upscale'):
            _adv.pop(_k, None)
    else:
        _what = {"long": "Long edge in pixels",
                 "short": "Short edge in pixels",
                 "percent": "Scale in percent"}[_mode]
        # The same domain the manifest's Resize column enforces
        # (_MANIFEST_RESIZE_RE): a percent must be finite and > 0 (float()
        # happily parses 'nan' and 'inf'), an edge a positive integer. An
        # out-of-domain NUMBER is asked again; text that is not a number
        # disables the resize: never guess a size (a typo must not become a
        # 1 px output).
        while True:
            _raw = ui.ask_text("review.resize_value", _what, "100" if _mode == "percent" else "")
            try:
                _value = float(_raw) if _mode == "percent" else int(_raw)
            except (TypeError, ValueError):
                ui.line("Invalid value — resize disabled for this run.")
                _mode, _value = "none", None
                break
            if _mode == "percent":
                _ok = (_value == _value and _value not in (float("inf"), float("-inf"))
                       and _value > 0)
            else:
                _ok = _value > 0
            if _ok:
                break
            ui.line("Must be a positive (and finite) number — try again.")
        if _mode == "none":
            for _k in ('resize_mode', 'resize_value', 'allow_upscale'):
                _adv.pop(_k, None)
        else:
            _adv['resize_mode'] = _mode
            _adv['resize_value'] = _value
            if ui.confirm("review.upscale", "Allow upscale (enlarge images smaller than the "
                          "target)?", False):
                _adv['allow_upscale'] = True
            else:
                _adv.pop('allow_upscale', None)
            if _mode == "percent" and _value > 100 and not _adv.get('allow_upscale'):
                ui.line("WARNING: a percentage above 100 upscales; the child refuses it "
                        "without --allow-upscale.")
    _sharpen = ui.choose("review.sharpen", "Output sharpening",
                         [("none", "none"), ("screen", "screen"), ("print", "print")],
                         "none")
    if _sharpen == "none":
        _adv.pop('sharpen', None)
    else:
        _adv['sharpen'] = _sharpen


def _derivative_in_place_rows(entries: List) -> List:
    """Manifest rows (source, destination, mode) that would run the
    recompressor IN PLACE: mode 8, or mode 0 whose Destination is empty or the
    Source itself (the builder then sends no output, or the same folder).
    --output-icc and --rename-from refuse those (exit 2 per row)."""
    rows = []
    for src, dst, mode in entries:
        if mode == 8:
            rows.append((src, dst, mode))
        elif mode == 0:
            same = not dst
            if not same:
                try:
                    same = (os.path.normcase(os.path.abspath(str(dst)))
                            == os.path.normcase(os.path.abspath(str(src))))
                except (OSError, ValueError):
                    same = True
            if same:
                rows.append((src, dst, mode))
    return rows


def _export_folder_name(origin: str, dest: str, override: Optional[str] = None) -> str:
    """The folder modes 6/7 create under the export marker, by direction.

    Read from the child's OWN setting (EXPORT_TIFF_FOLDER / EXPORT_JXL_FOLDER /
    EXPORT_JPEG_FOLDER at the top of each script), never hardcoded here: a name
    the user edited in the script is the folder the child really writes, so it
    is the one the delete panel must announce (round 43). The literal fallbacks
    are the scripts' shipped defaults, used only if the child cannot be
    imported. The delete gate used to hardcode "16B_JXL" for EVERY direction,
    so a JXL -> TIFF run about to erase originals was told they would land
    somewhere they never do (16B_TIFF). The whole point of that gate is that a
    wrong destination is visible before the user types the token.

    `override` is the run's `--export-jxl-folder` (the wrapper's
    `mode_config['export_jxl_folder']`): it only means something for the two
    directions whose child accepts the flag (TIFF -> JXL and JXL -> JXL).
    """
    if override and dest == 'jxl' and origin in ('tiff', 'jxl'):
        return override
    if dest == 'tiff':
        return _child_setting('jxl_tiff_decoder', 'EXPORT_TIFF_FOLDER', '16B_TIFF')
    if dest == 'jxl':
        if origin == 'tiff':
            return _child_setting('jxl_tiff_encoder', 'EXPORT_JXL_FOLDER', '16B_JXL')
        if origin == 'jxl':
            return _child_setting('jxl_recompressor', 'EXPORT_JXL_FOLDER', '16B_JXL_small')
        return _child_setting('jxl_jpeg_transcoder', 'EXPORT_JXL_FOLDER', 'JXL_jpeg')
    return _child_setting('jxl_jpeg_transcoder', 'EXPORT_JPEG_FOLDER', 'JPEG_recovered')


def _kill_process_tree(process) -> None:
    """Kill a child script AND what it started (W3 of the 2026-10-08 audit).

    On Windows Popen.kill() is TerminateProcess on the Python child alone: the
    cjxl/djxl/exiftool it launched kept running and left their
    <uuid>_name.tmp files beside the finals, where no sweep looks. taskkill /T
    takes the whole tree. Only a real Popen gets it: a pid of a stand-in
    object could name an unrelated process."""
    if os.name == "nt" and isinstance(process, subprocess.Popen):
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, timeout=30)
        except Exception:
            pass
    try:
        process.kill()
    except Exception:
        pass


def _child_setting(module: str, name: str, fallback):
    """A child script's own configured setting (the value at the top of that
    script), so the wrapper never shadows a user edit with a literal copy.
    `fallback` is the shipped default, used only when the child cannot be
    imported (a missing optional dependency)."""
    try:
        mod = importlib.import_module(module)
    except Exception:
        return fallback
    value = getattr(mod, name, None)
    return value if value else fallback


def _child_bool_setting(module: str, name: str, fallback: bool) -> bool:
    """_child_setting for a BOOLEAN: False is a real value, not "unset"
    (_child_setting's `value if value else fallback` would turn a user's
    False into the fallback). `fallback` only when the child cannot be
    imported or lacks the setting."""
    try:
        mod = importlib.import_module(module)
    except Exception:
        return fallback
    value = getattr(mod, name, None)
    return fallback if value is None else bool(value)


# The four child scripts whose TOOLKIT_VERSION must match the wrapper's.
_VERSIONED_CHILDREN = ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
                       "jxl_jpeg_transcoder.py", "jxl_recompressor.py")
_TOOLKIT_VERSION_RE = re.compile(r'^TOOLKIT_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)


def _script_version(path: Path):
    """TOOLKIT_VERSION as written in a script's source, or None (a script from
    before the constant existed, or unreadable). Read as TEXT, never imported:
    a child that fails to import must still be reported."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _TOOLKIT_VERSION_RE.search(text)
    return m.group(1) if m else None


def _mixed_version_messages(script_dir: Path) -> list:
    """One line per child script whose version differs from the wrapper's.
    A missing child is not reported here (the dependency check owns that)."""
    msgs = []
    for name in _VERSIONED_CHILDREN:
        p = script_dir / name
        if not p.exists():
            continue
        v = _script_version(p)
        if v != TOOLKIT_VERSION:
            shown = v if v else "an older version (no TOOLKIT_VERSION)"
            msgs.append(f"{name} is {shown}; jxl_photo.py is {TOOLKIT_VERSION}")
    return msgs


# Wizard option -> the setting at the top of the child script that is its
# default, with the shipped value as the fallback for a child that cannot be
# imported. The wizard puts these options on the child's command line, so a
# literal default in the wizard would override an edit of that setting.
_CHILD_OPTION_DEFAULTS = {
    'jxl_tiff_encoder': {
        'multipage_mode': ('MULTIPAGE_TIFF_MODE', 'split'),
        'thumbnail_mode': ('THUMBNAIL_MODE', 'auto'),
        'thumbnail_suffix': ('THUMBNAIL_SUFFIX', '_thumbnail'),
        'd50_patch': ('D50_PATCH_MODE', 'auto'),
        'encode_tag': ('ENCODE_TAG_MODE', 'xmp'),
        'use_ram': ('USE_RAM_FOR_PNG', True),
        'provenance': ('PROVENANCE_CHECK', 'path'),
        'overwrite_guard': ('OVERWRITE_GUARD', True),
        'distance': ('CJXL_DISTANCE', 0.1),
        'effort': ('CJXL_EFFORT', 7),
    },
    'jxl_tiff_decoder': {
        'thumbnail_handling': ('THUMBNAIL_HANDLING', 'include'),
        'thumbnail_suffix': ('THUMBNAIL_SUFFIX', '_thumbnail'),
        'depth_policy': ('DEPTH_POLICY', 'preserve_thumbnails'),
        'compression': ('TIFF_COMPRESSION', 'zip'),
        'bit_depth': ('DJXL_OUTPUT_DEPTH', 16),
        'add_preview': ('ADD_JPEG_PREVIEW', True),
        'reconstruct_multipage': ('RECONSTRUCT_MULTIPAGE', True),
        'provenance': ('PROVENANCE_CHECK', 'path'),
        'overwrite_guard': ('OVERWRITE_GUARD', True),
    },
    'jxl_jpeg_transcoder': {
        'bit_depth': ('PNG_DEFAULT_BIT_DEPTH', 16),
        'provenance': ('PROVENANCE_CHECK', 'path'),
        'overwrite_guard': ('OVERWRITE_GUARD', True),
        'quality': ('JPEG_DEFAULT_QUALITY', 95),
        'effort': ('CJXL_EFFORT', 7),
    },
    'jxl_recompressor': {
        'distance': ('CJXL_DISTANCE', 1.0),
        'effort': ('CJXL_EFFORT', 7),
        'provenance': ('PROVENANCE_CHECK', 'path'),
        'overwrite_guard': ('OVERWRITE_GUARD', True),
    },
}


def _child_number_setting(module: str, name: str, fallback):
    """_child_setting for a NUMBER: 0 is a real value (CJXL_DISTANCE = 0 is
    lossless), not "unset" — `value if value else fallback` would replace it
    with the fallback."""
    try:
        mod = importlib.import_module(module)
    except Exception:
        return fallback
    value = getattr(mod, name, None)
    return fallback if value is None else value


def _child_scan_distance() -> str:
    """The encoder's SCAN_DISTANCE as the wizard offers it: a number, or
    'same' when it is None (scans follow the run's distance). "0" only when
    the encoder cannot be imported."""
    try:
        mod = importlib.import_module('jxl_tiff_encoder')
    except Exception:
        return "0"
    value = getattr(mod, 'SCAN_DISTANCE', 0.0)
    return "same" if value is None else f"{float(value):g}"


def _valid_scan_distance(text: str):
    """A --scan-distance value the encoder accepts, normalised, or None."""
    t = str(text or "").strip().lower()
    if t == "same":
        return "same"
    try:
        d = float(t)
    except ValueError:
        return None
    return f"{d:g}" if 0 <= d <= 15 else None


# The encoder's --thumbnail-mode choices, and the ones that can write a
# thumbnail page (and so need its suffix).
_THUMBNAIL_MODES = ["auto", "scans", "exclude", "include"]
_THUMBNAIL_KEEPING_MODES = ("auto", "scans", "include")


def _child_default(module: str, option: str):
    """The child's own default for a wizard option (see _CHILD_OPTION_DEFAULTS).
    A remembered `last_*` answer still comes first at every call site."""
    name, fallback = _CHILD_OPTION_DEFAULTS[module][option]
    if isinstance(fallback, bool):
        return _child_bool_setting(module, name, fallback)
    if isinstance(fallback, (int, float)):
        return _child_number_setting(module, name, fallback)
    return _child_setting(module, name, fallback)


def _workflow_or_child(workflow: Dict, key: str, module: str):
    """The run's own answer for `key`, else the child script's setting —
    never a literal copy of the shipped default."""
    value = workflow.get(key)
    return value if value is not None else _child_default(module, key)


def _child_module(origin: str, dest: str) -> str:
    """The child script (module name) that runs this direction."""
    if origin == 'tiff' and dest == 'jxl':
        return 'jxl_tiff_encoder'
    if origin == 'jxl' and dest == 'tiff':
        return 'jxl_tiff_decoder'
    if origin == 'jxl' and dest == 'jxl':
        return 'jxl_recompressor'
    return 'jxl_jpeg_transcoder'


def _bit_depth_or_default(workflow: Dict, dest: str) -> int:
    """The run's bit depth: the user's answer, else the child's own default —
    the decoder's for TIFF, the transcoder's for PNG (one key, two children)."""
    module = 'jxl_tiff_decoder' if dest == 'tiff' else 'jxl_jpeg_transcoder'
    return workflow.get('bit_depth') or _child_default(module, 'bit_depth')


# === OPTION REGISTRY =========================================================
#
# One entry per flag this wrapper passes to a child script, and the single
# command builder (InteractiveMenu.build_child_cmd) that turns a run into a
# child's argv from it — the direct run and every manifest entry alike. Before
# this, the two builders were near-copies that had already drifted in what
# they emitted for the same run.
#
# Adding a flag the wrapper should pass: one Opt here (a policy entry when its
# rule needs more than "emit it when set"), the value in the workflow /
# advanced_options dict the wizard fills, and nothing else in the builder.
# tests/test_wrapper_option_registry.py fails when a child gains a flag that is
# neither here nor in NOT_OFFERED / MAINTENANCE_FLAGS, and when this registry
# names a flag a child does not accept. Defaults are never repeated here: the
# value comes from the run, else from the child (_workflow_or_child,
# _child_default), exactly as before.

DIRECTIONS = ("tiff2jxl", "jxl2tiff", "jxl2jxl", "jpeg2jxl", "jxl2jpeg", "jxl2png")
DIRECTION_MODULE = {
    "tiff2jxl": "jxl_tiff_encoder",
    "jxl2tiff": "jxl_tiff_decoder",
    "jxl2jxl": "jxl_recompressor",
    "jpeg2jxl": "jxl_jpeg_transcoder",
    "jxl2jpeg": "jxl_jpeg_transcoder",
    "jxl2png": "jxl_jpeg_transcoder",
}
_ALL_DIRECTIONS = frozenset(DIRECTIONS)
_TRANSCODER_DIRECTIONS = frozenset({"jpeg2jxl", "jxl2jpeg", "jxl2png"})
_DECODE_LOSSY_CONVERSIONS = ('jxl_to_jpeg_auto', 'jxl_to_jpeg_force', 'jxl_to_png')


def _direction(origin: str, dest: str) -> str:
    """The registry's name for a run's direction (the child is
    DIRECTION_MODULE[...]). Everything that is not TIFF/JXL<->JXL runs on the
    transcoder, as in _child_module."""
    if origin == 'tiff' and dest == 'jxl':
        return "tiff2jxl"
    if origin == 'jxl' and dest == 'tiff':
        return "jxl2tiff"
    if origin == 'jxl' and dest == 'jxl':
        return "jxl2jxl"
    if origin == 'jxl':
        return "jxl2png" if dest == 'png' else "jxl2jpeg"
    return "jpeg2jxl"


GROUPS = (("essentials", "ESSENTIALS"), ("existing", "EXISTING OUTPUTS"),
          ("deletion", "DELETION & IN-PLACE"), ("multipage", "MULTI-PAGE & SCANS"),
          ("colour", "COLOUR & SCANS"), ("metadata", "METADATA"),
          ("derivative", "DERIVATIVE (resize/colour/sharpen)"),
          ("files", "FILES & FOLDERS"), ("performance", "PERFORMANCE"),
          ("internal", "SET BY THE WRAPPER"))
LEVELS = ("essential", "advanced", "expert", "internal")


@dataclass(frozen=True)
class Opt:
    """One child flag the wrapper emits.

    kind decides how build_child_cmd emits it from its value:
      flag    -> [flag] when the value is truthy
      value   -> [flag, str(value)] when the value is truthy
      notnone -> [flag, str(value)] when the value is not None (0 is a value)
      always  -> [flag, str(value)] every time
      tri     -> [flag] for True, [neg_flag] for False, nothing for None
      switch  -> [flag] when truthy, else [neg_flag]
      policy  -> emitted by the named InteractiveMenu._policy_* rule, which
                 also emits extra_flags; the entry documents the flag
    The value is `value(ctx)` when given, else the dict slot `legacy_key`
    names ("advanced.x", "workflow.x", "mode_config.x", or "extra.x" — the
    run's extra_options, the options the wizard does not ask about). `when(ctx)`
    gates the entry beyond its directions; `requires`/`conflicts` name other
    ids (_registry_rule_problem checks them before a run).
    """
    id: str
    flag: str
    directions: frozenset
    kind: str
    group: str
    level: str
    label: str
    help: str
    legacy_key: str = ""
    neg_flag: str = ""
    choices: tuple = ()
    when: Optional[Callable] = None
    value: Optional[Callable] = None
    policy: str = ""
    extra_flags: tuple = ()
    danger: bool = False
    requires: tuple = ()        # ids that must be on with this one
    conflicts: tuple = ()       # ids that must be off with this one


class _CmdCtx:
    """What build_child_cmd's rules look at, computed once per command."""

    def __init__(self, origin, dest, source, dest_path, mode, workers,
                 workflow, advanced, manifest, config_marker):
        self.origin, self.dest = origin, dest
        self.direction = _direction(origin, dest)
        self.module = DIRECTION_MODULE[self.direction]
        self.source, self.dest_path = source, dest_path
        self.mode, self.workers = mode, workers
        self.workflow = workflow
        self.advanced = advanced if advanced is not None else {}
        self.mode_config = workflow.get('mode_config', {}) or {}
        self.extra = workflow.get('extra_options') or {}
        self.manifest = manifest
        self.config_marker = config_marker
        self.conv_type = workflow.get('conversion_type', '')
        self.derivative = _has_derivative_options(self.advanced)
        # The transcoder's decode conversions that shape pixels (resize,
        # sharpening, rename apply); the lossless recovery cannot.
        self.decode_lossy = (origin == 'jxl' and dest in ('jpeg', 'png')
                             and self.conv_type in _DECODE_LOSSY_CONVERSIONS)

    def get(self, legacy_key: str):
        where, _, key = legacy_key.partition('.')
        source = {"advanced": self.advanced, "workflow": self.workflow,
                  "mode_config": self.mode_config, "extra": self.extra}[where]
        return source.get(key)


def _opt_value(opt: "Opt", ctx: _CmdCtx):
    return opt.value(ctx) if opt.value is not None else ctx.get(opt.legacy_key)


def _emit_opt(opt: "Opt", ctx: _CmdCtx) -> List[str]:
    """The argv tokens one non-policy entry contributes (see Opt.kind)."""
    v = _opt_value(opt, ctx)
    if opt.kind == "flag":
        return [opt.flag] if v else []
    if opt.kind == "value":
        return [opt.flag, str(v)] if v else []
    if opt.kind == "notnone":
        return [opt.flag, str(v)] if v is not None else []
    if opt.kind == "always":
        return [opt.flag, str(v)]
    if opt.kind == "tri":
        return [opt.flag] if v is True else ([opt.neg_flag] if v is False else [])
    if opt.kind == "switch":
        return [opt.flag] if v else [opt.neg_flag]
    raise ValueError(f"{opt.id}: kind {opt.kind!r} is not emitted generically")


def _resize_value(mode: str):
    """value() of a --resize-* entry: the size when that resize mode is set."""
    def _v(ctx):
        a = ctx.advanced
        return a.get('resize_value') if a.get('resize_mode') == mode else None
    return _v


def _shaping_applies(ctx) -> bool:
    """Resize/sharpening/upscale: always on the recompressor, only on the
    transcoder's lossy decode (the bit-exact recovery cannot take them)."""
    return ctx.direction == "jxl2jxl" or ctx.decode_lossy


_D = frozenset
_TIFF2JXL, _JXL2TIFF, _JXL2JXL = _D({"tiff2jxl"}), _D({"jxl2tiff"}), _D({"jxl2jxl"})
_JPEG_OUT = _D({"jxl2jpeg"})

REGISTRY: Tuple[Opt, ...] = (
    # -- set by the wrapper on every run --------------------------------------
    Opt("mode", "--mode", _ALL_DIRECTIONS, "always", "internal", "internal",
        "Mode", "Output layout (0-8), from the wizard's mode step.",
        value=lambda c: c.mode, choices=tuple(range(9))),
    Opt("workers", "--workers", _ALL_DIRECTIONS, "always", "performance", "essential",
        "Workers", "Files converted in parallel.", value=lambda c: c.workers),
    Opt("summary_json", "--summary-json", _ALL_DIRECTIONS, "flag", "internal", "internal",
        "Summary line", "Machine-readable totals, summed by the manifest recap "
        "(manifest entries only).", value=lambda c: c.manifest),
    Opt("export_marker", "--export-marker", _ALL_DIRECTIONS, "value", "files", "advanced",
        "Export marker", "Folder name that anchors modes 6/7. Always passed, the "
        "default included, so the child anchors on the marker the wrapper used.",
        value=lambda c: c.mode_config.get('export_marker') or c.config_marker),
    Opt("export_subfolder", "--export-subfolder", _ALL_DIRECTIONS, "value", "files",
        "advanced", "Export subfolder", "Mode 7: the subfolder below the marker.",
        legacy_key="mode_config.export_subfolder"),
    Opt("export_jxl_folder", "--export-jxl-folder", _D({"tiff2jxl", "jxl2jxl"}), "value",
        "files", "advanced", "Modes 6/7 output folder",
        "Name of the JXL folder modes 6/7 write next to the marker.",
        legacy_key="mode_config.export_jxl_folder"),
    Opt("exclude_folders", "--exclude-folders", _D({"tiff2jxl", "jxl2tiff"}), "policy",
        "files", "advanced", "Exclude folders",
        "';'-separated folder names skipped below the input root. Other "
        "directions warn that it is ignored.", policy="_policy_exclude_folders"),
    Opt("dry_run", "--dry-run", _ALL_DIRECTIONS, "flag", "essentials", "essential",
        "Dry run", "Simulate: report what would happen, write nothing.",
        legacy_key="workflow.dry_run"),
    Opt("staging", "--staging", _ALL_DIRECTIONS, "value", "performance", "advanced",
        "Staging folder", "Write outputs to a fast local folder first, then move them.",
        legacy_key="workflow.staging"),
    # -- existing outputs ------------------------------------------------------
    Opt("overwrite", "--overwrite", _ALL_DIRECTIONS, "flag", "existing", "essential",
        "Overwrite", "Always rewrite existing outputs.", legacy_key="advanced.overwrite"),
    Opt("sync", "--sync", _ALL_DIRECTIONS, "flag", "existing", "essential",
        "Sync", "Only rewrite outputs older than their source.",
        legacy_key="advanced.sync"),
    Opt("provenance", "--provenance", _ALL_DIRECTIONS, "policy", "existing", "advanced",
        "Provenance check", "How an existing output is matched to its source "
        "(adopt: TIFF -> JXL only; elsewhere downgraded to path).",
        choices=('path', 'content', 'adopt'), policy="_append_provenance_flags"),
    Opt("adopt_scan", "--no-adopt-scan", _TIFF2JXL, "policy", "existing", "expert",
        "Adopt without the scan", "With --provenance adopt: stamp legacy archives "
        "without decoding them first.", policy="_append_provenance_flags", danger=True),
    Opt("overwrite_guard", "--no-overwrite-guard", _ALL_DIRECTIONS, "policy", "existing",
        "advanced", "Overwrite guard off", "Allow overwriting an output whose marker "
        "names another source (renamed folders). Never with a delete.",
        policy="_append_overwrite_guard_flag", danger=True),
    # -- deletion & in-place ---------------------------------------------------
    Opt("delete_source", "--delete-source", _ALL_DIRECTIONS, "policy", "deletion",
        "essential", "Delete originals", "Delete each source once its output is "
        "verified. The wrapper charges the HHMM token, so the child's own "
        "confirmation is turned off.", policy="_policy_delete",
        extra_flags=("--delete-confirm-off",), danger=True,
        legacy_key="advanced.delete_source"),
    Opt("verify_roundtrip", "--verify-roundtrip", _D({"tiff2jxl", "jxl2jxl"}), "policy",
        "deletion", "advanced", "Round-trip check before deleting",
        "Decode each output and compare it with the source before the delete.",
        policy="_policy_delete"),
    Opt("delete_skipped", "--delete-skipped", _ALL_DIRECTIONS, "policy", "deletion",
        "advanced", "Also delete skipped sources", "Delete sources whose output "
        "already existed (proved by provenance). Only with --delete-source.",
        policy="_policy_delete", danger=True, legacy_key="advanced.delete_skipped"),
    # -- TIFF -> JXL -----------------------------------------------------------
    Opt("distance", "--distance", _D({"tiff2jxl", "jxl2jxl"}), "always", "essentials",
        "essential", "Distance", "cjxl distance (0 = lossless).",
        value=lambda c: _workflow_or_child(c.workflow, 'distance', c.module)),
    Opt("effort", "--effort", _ALL_DIRECTIONS - _JXL2TIFF, "always", "essentials",
        "essential", "Effort", "cjxl effort 1-10.", choices=tuple(range(1, 11)),
        value=lambda c: _workflow_or_child(c.workflow, 'effort', c.module)),
    Opt("use_ram", "--ram", _TIFF2JXL, "switch", "performance", "advanced",
        "PNG in RAM", "Hand cjxl the intermediate PNG through memory.",
        neg_flag="--no-ram", legacy_key="workflow.use_ram"),
    Opt("strip", "--strip", _TIFF2JXL, "flag", "metadata", "advanced",
        "Strip metadata", "Write no EXIF/XMP into the JXL.", legacy_key="advanced.strip"),
    Opt("d50_patch", "--d50-patch", _TIFF2JXL, "value", "colour", "expert",
        "D50 patch", "ICC D50 white-point patch.", choices=('on', 'off', 'auto'),
        legacy_key="advanced.d50_patch"),
    Opt("modular", "--modular", _TIFF2JXL, "value", "performance", "expert",
        "Modular mode", "Force the Modular encoder for lossy output — screenshots and "
        "graphics only: on photos, equal quality, bigger files, 20-100x slower.",
        choices=('on', 'off'), legacy_key="advanced.modular"),
    Opt("encode_tag", "--encode-tag", _TIFF2JXL, "value", "metadata", "advanced",
        "Encode record", "Where the cjxl d=/e= record is written.",
        choices=('xmp', 'software', 'off'), legacy_key="advanced.encode_tag"),
    Opt("multipage_mode", "--multipage-mode", _TIFF2JXL, "value", "multipage", "advanced",
        "Multi-page TIFFs", "What happens to the pages after the first: split = one "
        "JXL per page, thumbnails per the next option (the shipped setting); split_all = "
        "every page, thumbnails too; skip = leave multi-page TIFFs alone; ignore = encode "
        "page 0 only, DROP the rest (an IR page of a film scan included).",
        choices=('ignore', 'skip', 'split', 'split_all'),
        legacy_key="advanced.multipage_mode"),
    Opt("thumbnail_mode", "--thumbnail-mode", _TIFF2JXL, "value", "multipage", "advanced",
        "Thumbnail pages", "Which embedded thumbnail pages are encoded.",
        choices=tuple(_THUMBNAIL_MODES), legacy_key="advanced.thumbnail_mode"),
    Opt("thumbnail_suffix", "--thumbnail-suffix", _D({"tiff2jxl", "jxl2tiff"}), "value",
        "multipage", "expert", "Thumbnail suffix", "File-name suffix of a thumbnail page.",
        legacy_key="advanced.thumbnail_suffix"),
    Opt("scan_distance", "--scan-distance", _TIFF2JXL, "value", "colour", "advanced",
        "Film-scan distance", "Distance for film scans ('same' = the run's).",
        legacy_key="advanced.scan_distance"),
    # -- JXL -> TIFF -----------------------------------------------------------
    Opt("compression", "--compression", _JXL2TIFF, "always", "essentials", "advanced",
        "TIFF compression", "Compression of the decoded TIFF.",
        choices=('zip', 'lzw', 'none', 'uncompressed'),
        value=lambda c: (c.workflow.get('compression')
                         or _child_default('jxl_tiff_decoder', 'compression'))),
    Opt("tiff_depth", "--depth", _JXL2TIFF, "always", "essentials", "essential",
        "TIFF bit depth", "8 or 16 bits per sample.", choices=(8, 16),
        value=lambda c: _bit_depth_or_default(c.workflow, 'tiff')),
    Opt("no_preview", "--no-preview", _JXL2TIFF, "flag", "metadata", "advanced",
        "No JPEG preview", "Do not embed the JPEG preview in the TIFF.",
        value=lambda c: c.workflow.get('add_preview') is False),
    Opt("matrix", "--matrix", _JXL2TIFF, "flag", "colour", "expert",
        "Matrix decode", "Decode through a matrix profile.", legacy_key="advanced.matrix"),
    Opt("none", "--none", _JXL2TIFF, "flag", "colour", "expert",
        "No colour management", "Write no profile (degraded copy: never deletes).",
        legacy_key="advanced.none", when=lambda c: not c.advanced.get('matrix')),
    Opt("basic", "--basic", _JXL2TIFF, "flag", "colour", "expert",
        "Basic decode", "Decode without the XMP profile.", legacy_key="advanced.basic",
        when=lambda c: not (c.advanced.get('matrix') or c.advanced.get('none'))),
    Opt("target_icc", "--target-icc", _JXL2TIFF, "value", "colour", "expert",
        "Target ICC", "Convert the decode to this profile.",
        legacy_key="advanced.target_icc"),
    Opt("no_icc_cleanup", "--no-icc-cleanup", _JXL2TIFF, "flag", "colour", "expert",
        "Keep the carried ICC", "Leave the jxlphoto-iccprofile token in the TIFF.",
        legacy_key="advanced.no_icc_cleanup"),
    Opt("thumbnail_handling", "--thumbnail-handling", _JXL2TIFF, "value", "multipage",
        "advanced", "Thumbnail pages", "What happens to thumbnail pages on decode.",
        choices=('ignore', 'include', 'generate'),
        legacy_key="advanced.thumbnail_handling"),
    Opt("no_reconstruct_multipage", "--no-reconstruct-multipage", _JXL2TIFF, "flag",
        "multipage", "advanced", "Keep pages apart",
        "Decode split pages as single TIFFs.", legacy_key="advanced.no_reconstruct_multipage"),
    Opt("depth_policy", "--depth-policy", _JXL2TIFF, "value", "multipage", "expert",
        "Page depth policy", "Bit depth of reconstructed pages.",
        choices=('force16', 'preserve_thumbnails', 'preserve_original'),
        legacy_key="advanced.depth_policy"),
    # -- JXL -> JXL ------------------------------------------------------------
    Opt("on_downgrade", "--on-downgrade", _JXL2JXL, "value", "existing", "advanced",
        "Counterproductive re-encode", "What to do when the request is no better "
        "than the file.", choices=('ask', 'copy', 'skip', 'convert'),
        legacy_key="advanced.on_downgrade"),
    Opt("on_regeneration", "--on-regeneration", _JXL2JXL, "value", "existing",
        "advanced", "Repeat lossy generation", "What to do with a gen >= 2 lossy "
        "request.", choices=('ask', 'copy', 'skip', 'convert'),
        legacy_key="advanced.on_regeneration"),
    Opt("on_unknown", "--on-unknown", _JXL2JXL, "value", "existing", "advanced",
        "Unknown origin", "What to do with a JXL without an encode record.",
        choices=('ask', 'copy', 'skip', 'convert'), legacy_key="advanced.on_unknown"),
    Opt("on_scan", "--on-scan", _JXL2JXL, "value", "existing", "advanced",
        "Film scans", "What to do with a film scan.",
        choices=('ask', 'copy', 'skip', 'convert'), legacy_key="advanced.on_scan"),
    Opt("jbrd_policy", "--jbrd-policy", _JXL2JXL, "value", "existing", "advanced",
        "JPEG archives", "What to do with a lossless JPEG archive (jbrd).",
        choices=('copy', 'skip', 'convert'), legacy_key="advanced.jbrd_policy"),
    Opt("no_keep_smaller", "--no-keep-smaller", _JXL2JXL, "flag", "existing", "expert",
        "Keep a larger result", "Keep the re-encode even when it is larger.",
        legacy_key="advanced.no_keep_smaller"),
    Opt("rederive_on_encode_change", "--rederive-on-encode-change", _JXL2JXL, "tri",
        "derivative", "expert", "Re-derive on d/e change",
        "Re-derive a derivative whose recorded distance/effort differs.",
        neg_flag="--no-rederive-on-encode-change", when=lambda c: c.derivative,
        legacy_key="advanced.rederive_on_encode_change"),
    Opt("rederive_on_lower_effort", "--rederive-on-lower-effort", _JXL2JXL, "tri",
        "derivative", "expert", "Re-derive on lower effort",
        "Re-derive when only the effort is lower.",
        neg_flag="--no-rederive-on-lower-effort", when=lambda c: c.derivative,
        legacy_key="advanced.rederive_on_lower_effort"),
    Opt("output_icc", "--output-icc", _JXL2JXL, "value", "derivative", "advanced",
        "Output profile", "16-bit colour conversion to this profile (a derivative).",
        legacy_key="advanced.output_icc"),
    # -- derivative shaping (recompressor; transcoder's lossy decode) ----------
    Opt("resize_long", "--resize-long", _D({"jxl2jxl", "jxl2jpeg", "jxl2png"}), "value",
        "derivative", "advanced", "Resize: long side", "Long side in pixels.",
        value=_resize_value('long'), when=_shaping_applies),
    Opt("resize_short", "--resize-short", _D({"jxl2jxl", "jxl2jpeg", "jxl2png"}), "value",
        "derivative", "advanced", "Resize: short side", "Short side in pixels.",
        value=_resize_value('short'), when=_shaping_applies),
    Opt("resize_percent", "--resize-percent", _D({"jxl2jxl", "jxl2jpeg", "jxl2png"}),
        "value", "derivative", "advanced", "Resize: percent", "Scale in percent.",
        value=_resize_value('percent'), when=_shaping_applies),
    Opt("allow_upscale", "--allow-upscale", _D({"jxl2jxl", "jxl2jpeg", "jxl2png"}), "flag",
        "derivative", "expert", "Allow upscaling", "Let a resize enlarge a smaller file.",
        legacy_key="advanced.allow_upscale", when=_shaping_applies),
    Opt("sharpen", "--sharpen", _D({"jxl2jxl", "jxl2jpeg", "jxl2png"}), "value",
        "derivative", "advanced", "Sharpening", "Output sharpening preset.",
        choices=('none', 'screen', 'print'), when=_shaping_applies,
        value=lambda c: (c.advanced['sharpen']
                         if (c.advanced.get('sharpen') or 'none') != 'none' else None)),
    Opt("rename_from", "--rename-from", _D({"jxl2jxl", "jxl2jpeg", "jxl2png"}), "policy",
        "files", "advanced", "Rename outputs", "Replace a token in each output name "
        "(planned up front; never in place).", policy="_policy_rename",
        extra_flags=("--rename-to",)),
    # -- transcoder ------------------------------------------------------------
    Opt("conversion", "--force-transcode", _TRANSCODER_DIRECTIONS, "policy",
        "essentials", "internal", "Conversion type",
        "The wizard's conversion type, as --force-transcode / --force-convert / "
        "--from-jpeg / --from-jxl / --decode.", policy="_policy_conversion",
        extra_flags=("--force-convert", "--from-jpeg", "--from-jxl", "--decode")),
    Opt("jpeg_distance", "--distance", _D({"jpeg2jxl"}), "notnone", "essentials",
        "essential", "Distance", "cjxl distance of a lossy JPEG -> JXL.",
        legacy_key="workflow.distance", when=lambda c: c.conv_type == 'convert_lossy'),
    Opt("quality", "--quality", _JPEG_OUT, "always", "essentials", "essential",
        "JPEG quality", "Quality of a JPEG output.",
        when=lambda c: c.origin == 'jxl' and c.dest == 'jpeg',
        value=lambda c: _workflow_or_child(c.workflow, 'quality', 'jxl_jpeg_transcoder')),
    Opt("icc_profile", "--icc-profile", _TRANSCODER_DIRECTIONS, "value", "colour",
        "advanced", "Output profile", "Convert to this profile (a derivative: never "
        "deletes).", legacy_key="workflow.icc_profile"),
    Opt("no_md5", "--no-md5", _TRANSCODER_DIRECTIONS, "flag", "metadata", "expert",
        "No checksums", "Do not record checksums.md5 lines.", legacy_key="advanced.no_md5",
        danger=True),
    Opt("no_verify", "--no-verify", _TRANSCODER_DIRECTIONS, "flag", "deletion", "expert",
        "Skip verification", "Do not verify outputs.", legacy_key="advanced.no_verify",
        danger=True),
    Opt("auto_repair_jbrd", "--auto-repair-jbrd", _TRANSCODER_DIRECTIONS, "flag",
        "existing", "advanced", "Repair jbrd archives",
        "Repair archives written by affected versions while converting.",
        legacy_key="advanced.auto_repair_jbrd"),
    Opt("output_suffix", "--output-suffix", _TRANSCODER_DIRECTIONS, "value", "files",
        "advanced", "Output folder suffix", "Mode 2's folder suffix.",
        legacy_key="advanced.output_suffix"),
    Opt("format", "--format", _D({"jxl2jpeg", "jxl2png"}), "policy", "essentials",
        "internal", "Output format", "jpeg or png, from the destination.",
        choices=('jpeg', 'png'), policy="_policy_format"),
    Opt("png_bit_depth", "--bit-depth", _D({"jxl2png"}), "value", "essentials",
        "essential", "PNG bit depth", "8 or 16 bits per sample.", choices=(8, 16),
        legacy_key="workflow.bit_depth", when=lambda c: c.dest == 'png'),
    # -- the options the wizard does not ask about (run's extra_options; the
    #    "all options" screen of the Step 7 summary sets them) ---------------
    Opt("buffering", "--buffering", _D({"tiff2jxl", "jxl2jxl"}), "notnone",
        "performance", "advanced", "cjxl buffering",
        "libjxl >= 0.12 streaming level: 1 needs about a third of the memory per "
        "worker; 0 compresses best and is much slower.", choices=(0, 1, 2, 3),
        legacy_key="extra.buffering"),
    Opt("no_preflight", "--no-preflight", _D({"tiff2jxl", "jxl2jxl"}), "flag",
        "performance", "advanced", "Skip the disk-space check",
        "Do not check the free space before the run.", legacy_key="extra.no_preflight"),
    Opt("clean_staging", "--clean-staging", _ALL_DIRECTIONS, "flag", "performance",
        "advanced", "Sweep old staging leftovers",
        "Delete leftovers idle for over an hour from the staging folder before the "
        "run (nothing while another run uses it).", legacy_key="extra.clean_staging",
        when=lambda c: bool(c.workflow.get('staging'))),
    Opt("icc_png_strategy", "--icc-png-strategy", _TIFF2JXL, "value", "colour",
        "advanced", "ICC in the intermediate PNG",
        "How a lossy encode hands the TIFF's profile to cjxl (cautious tests each "
        "profile once).", choices=("heuristic", "always", "skip", "cautious"),
        legacy_key="extra.icc_png_strategy"),
    Opt("scan_scope", "--scan-scope", _D({"tiff2jxl", "jxl2jxl"}), "value", "colour",
        "advanced", "Which scans keep their own distance",
        "all = every film scan; raw = only raw scans (finished ones follow the run).",
        choices=("all", "raw"), legacy_key="extra.scan_scope"),
    Opt("warn_thumbnail_discard", "--warn-thumbnail-discard", _TIFF2JXL, "flag",
        "multipage", "expert", "List dropped thumbnail pages",
        "Name every file whose embedded thumbnail page was not encoded.",
        legacy_key="extra.warn_thumbnail_discard"),
    Opt("restamp_then_sync", "--restamp-then-sync", _TIFF2JXL, "flag", "existing",
        "advanced", "Fix renamed folders, then sync",
        "Rewrite the path record of masters whose TIFF is unchanged (renamed or moved "
        "folders), then sync. Never with a delete or Overwrite.",
        legacy_key="extra.restamp_then_sync",
        conflicts=("delete_source", "delete_skipped", "overwrite")),
    Opt("explorer_thumbnail", "--explorer-thumbnail", _D({"tiff2jxl", "jxl2jxl"}),
        "value", "metadata", "advanced", "Explorer thumbnail",
        "auto = lossless outputs (every output when jxl-thumb is installed); on; off.",
        choices=("auto", "on", "off"), legacy_key="extra.explorer_thumbnail"),
    Opt("recompress_encode_tag", "--encode-tag", _JXL2JXL, "value", "metadata",
        "advanced", "Encode record", "Where the recompressor writes the cjxl d=/e= "
        "record.", choices=('xmp', 'software', 'off'),
        legacy_key="extra.recompress_encode_tag"),
    Opt("allow_incomplete_groups", "--allow-incomplete-groups", _JXL2TIFF, "flag",
        "deletion", "expert", "Delete incomplete multi-page groups",
        "Let the delete remove the JXLs of a multi-page document with missing pages "
        "(its TIFF has fewer pages). Only with Delete originals.",
        legacy_key="extra.allow_incomplete_groups", requires=("delete_source",),
        danger=True),
    Opt("compress_metadata", "--compress-metadata", _D({"jpeg2jxl"}), "flag", "metadata",
        "advanced", "Compress EXIF/XMP",
        "Keep cjxl's compressed metadata boxes: a smaller file, but IrfanView shows "
        "no EXIF.", legacy_key="extra.compress_metadata"),
    Opt("output_name", "--output-name", _TRANSCODER_DIRECTIONS, "value", "files",
        "advanced", "Output folder name", "Modes 1/3 of a lossy conversion: the "
        "subfolder's name.", legacy_key="extra.output_name",
        when=lambda c: c.mode in (1, 3)),
    Opt("sharpen_sigma", "--sharpen-sigma", _D({"jxl2jxl", "jxl2jpeg", "jxl2png"}),
        "notnone", "derivative", "expert", "Sharpening radius (sigma)",
        "Overrides the preset's unsharp-mask sigma.", legacy_key="extra.sharpen_sigma",
        when=lambda c: _shaping_applies(c)
        and (c.advanced.get('sharpen') or 'none') != 'none'),
    Opt("sharpen_gain", "--sharpen-gain", _D({"jxl2jxl", "jxl2jpeg", "jxl2png"}),
        "notnone", "derivative", "expert", "Sharpening amount (gain)",
        "Overrides the preset's unsharp-mask gain.", legacy_key="extra.sharpen_gain",
        when=lambda c: _shaping_applies(c)
        and (c.advanced.get('sharpen') or 'none') != 'none'),
    Opt("sharpen_threshold", "--sharpen-threshold",
        _D({"jxl2jxl", "jxl2jpeg", "jxl2png"}), "notnone", "derivative", "expert",
        "Sharpening threshold", "Overrides the preset's unsharp-mask threshold.",
        legacy_key="extra.sharpen_threshold",
        when=lambda c: _shaping_applies(c)
        and (c.advanced.get('sharpen') or 'none') != 'none'),
)

# Child flags the job builder does not pass, and why. Phase 2 of the wrapper
# redesign offers most of them (plan 261103, section 4).
NOT_OFFERED: Dict[str, str] = {
    "jxl_tiff_encoder:--icc-cache-dir": "Phase 4 (machine setting)",
    "jxl_tiff_encoder:--embed-thumbnail": "deprecated",
    "jxl_jpeg_transcoder:--ram": "no effect: accepted for compatibility (decode uses temp files)",
    "jxl_jpeg_transcoder:--no-ram": "no effect: accepted for compatibility (decode uses temp files)",
    "jxl_jpeg_transcoder:--to-srgb": "alias of --icc-profile sRGB, which the wrapper uses",
    # Never passed: the run's own answer is given another way.
    "jxl_tiff_encoder:--version": "not a run option",
    "jxl_tiff_decoder:--version": "not a run option",
    "jxl_jpeg_transcoder:--version": "not a run option",
    "jxl_recompressor:--version": "not a run option",
}

# Flags the maintenance screens pass (their own small commands, not jobs).
MAINTENANCE_FLAGS: Dict[str, str] = {
    "jxl_jpeg_transcoder:--repair-jbrd": "repair_jbrd_flow",
    "jxl_recompressor:--refresh-explorer-thumbnails": "explorer_thumbnail_flow",
    "jxl_tiff_encoder:--restamp-moved": "restamp_flow (menu 5)",
    "jxl_tiff_encoder:--clear-icc-cache": "clear_icc_cache_flow (menu 8)",
}


def _registry_flags(direction: str) -> Set[str]:
    """Every flag the registry can emit for one direction (policy extras and
    negative forms included)."""
    out = set()
    for opt in REGISTRY:
        if direction in opt.directions:
            out.add(opt.flag)
            if opt.neg_flag:
                out.add(opt.neg_flag)
            out.update(opt.extra_flags)
    return out


def _opt_is_on(opt: "Opt", ctx: "_CmdCtx") -> bool:
    """Is this entry set for the run (applicable, and with a value)?"""
    if ctx.direction not in opt.directions or not (opt.legacy_key or opt.value):
        return False
    if opt.when is not None and not opt.when(ctx):
        return False
    v = _opt_value(opt, ctx)
    return v is not None if opt.kind == "notnone" else bool(v)


def _registry_rule_problem(workflow: Dict) -> Optional[str]:
    """Why the run's options cannot go together (an entry's `conflicts` or
    `requires`), or None. Checked before a run and before the HHMM token, so
    the child does not exit 2 after the token (e.g. --restamp-then-sync with
    a delete)."""
    origin, dest = workflow.get('origin_format'), workflow.get('dest_format')
    if not origin or not dest:
        return None
    ctx = _CmdCtx(origin, dest, workflow.get('input_dir'), None, workflow.get('mode'),
                  workflow.get('workers'), workflow,
                  workflow.get('advanced_options') or {}, False, None)
    on = {o.id for o in REGISTRY if _opt_is_on(o, ctx)}
    label = {o.id: o.label for o in REGISTRY}
    for o in REGISTRY:
        if o.id not in on:
            continue
        bad = [label.get(c, c) for c in o.conflicts if c in on]
        if bad:
            return f"'{o.label}' ({o.flag}) cannot run with: {', '.join(bad)}"
        missing = [label.get(r, r) for r in o.requires if r not in on]
        if missing:
            return f"'{o.label}' ({o.flag}) needs: {', '.join(missing)}"
    return None


def _dest_is_source_file(source, dest_path, mode) -> bool:
    """A file Source whose Destination cell is the file itself (how the
    manifest loader normalizes an empty cell): it must NOT be sent as the
    output positional, or the child reads it as an output DIRECTORY named
    after the file. Only mode 0 can carry it."""
    if not dest_path or mode != 0:
        return False
    try:
        src_p = Path(source)
        if src_p.is_file():
            return (os.path.normcase(os.path.abspath(str(dest_path)))
                    == os.path.normcase(os.path.abspath(str(src_p))))
    except (OSError, ValueError):
        return False
    return False

# === END OF OPTION REGISTRY ==================================================


# === SCREENS BUILT ON THE REGISTRY ===========================================
#
# One question API for the screens added with the option registry (the Step 7
# "all options" screen, the restamp and ICC-cache maintenance flows). Each
# question carries a stable id (qid): ScriptedUI answers by id in the tests, so
# re-ordering a question never breaks a test that checks a rule. The rest of
# the wizard still asks through Rich/input() directly (phase 3 of the plan).


_KEEP = object()      # _ask_option_value: leave the option as it is


class UI:
    """The question/message interface. Subclasses render it."""

    def section(self, title: str) -> None:
        raise NotImplementedError

    def line(self, text: str = "") -> None:
        raise NotImplementedError

    def warn(self, text: str) -> None:
        self.line(f"WARNING: {text}")

    def ask_text(self, qid: str, prompt: str, default: str = "") -> str:
        raise NotImplementedError

    def confirm(self, qid: str, prompt: str, default: bool = False) -> bool:
        raise NotImplementedError

    def choose(self, qid: str, prompt: str, options: List[Tuple[str, str]],
               default: Optional[str] = None) -> str:
        """One key of `options` [(key, label)]."""
        raise NotImplementedError


class PlainUI(UI):
    def section(self, title):
        print(f"\n--- {title} ---")

    def line(self, text=""):
        print(text)

    def ask_text(self, qid, prompt, default=""):
        shown = f" [{default}]" if default else ""
        answer = input(f"{prompt}{shown}: ").strip()
        return answer if answer else default

    def confirm(self, qid, prompt, default=False):
        answer = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not answer else answer.startswith('y')

    def choose(self, qid, prompt, options, default=None):
        keys = [k for k, _l in options]
        for k, label in options:
            print(f"  [{k}] {label}")
        while True:
            answer = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
            if not answer and default is not None:
                return default
            if answer in keys:
                return answer
            print(f"Choose one of: {', '.join(keys)}")


class RichUI(UI):
    def section(self, title):
        console.print(f"\n[bold cyan]{_rich_safe(title)}[/bold cyan]")

    def line(self, text=""):
        console.print(_rich_safe(text))

    def warn(self, text):
        console.print(f"[yellow]{_rich_safe(text)}[/yellow]")

    def ask_text(self, qid, prompt, default=""):
        return Prompt.ask(_rich_safe(prompt), default=default or "").strip()

    def confirm(self, qid, prompt, default=False):
        return Confirm.ask(_rich_safe(prompt), default=default)

    def choose(self, qid, prompt, options, default=None):
        for k, label in options:
            console.print(f"  [bold cyan]{_rich_safe(k)}[/bold cyan]  {_rich_safe(label)}")
        return Prompt.ask(_rich_safe(prompt), choices=[k for k, _l in options],
                          default=default)


class ScriptedUI(UI):
    """For tests: answers = {qid: value or [values, consumed in order]}. A
    question with no answer left is an error, never a silent default."""

    def __init__(self, answers: Dict[str, Any]):
        self.answers = {k: (list(v) if isinstance(v, list) else [v])
                        for k, v in answers.items()}
        self.output: List[str] = []
        self.asked: List[str] = []

    def _next(self, qid):
        self.asked.append(qid)
        left = self.answers.get(qid)
        if not left:
            raise AssertionError(f"ScriptedUI: no answer for {qid!r}")
        return left.pop(0)

    def section(self, title):
        self.output.append(f"== {title}")

    def line(self, text=""):
        self.output.append(text)

    def warn(self, text):
        self.output.append(f"WARNING: {text}")

    def ask_text(self, qid, prompt, default=""):
        v = self._next(qid)
        return default if v is None else str(v)

    def confirm(self, qid, prompt, default=False):
        v = self._next(qid)
        return default if v is None else bool(v)

    def choose(self, qid, prompt, options, default=None):
        v = self._next(qid)
        v = default if v is None else str(v)
        if v not in [k for k, _l in options]:
            raise AssertionError(f"ScriptedUI: {v!r} is not an option of {qid!r}")
        return v


def _choice_keys(values) -> Tuple[List[str], str]:
    """The keys a choice prompt offers for `values`, and the key of "the
    default" (#606). A choice made of numbers is typed as the number itself
    ('none' for None) and '-' is the default: listed by position, typing 1
    for "--buffering 1" set level 0 (the slowest, most memory-hungry one) and
    a decoder depth of 16 could not be typed at all. Any other choice is
    picked by position, 0 being the default."""
    values = list(values)
    numeric = [v for v in values if v is not None]
    if numeric and all(isinstance(v, int) and not isinstance(v, bool) for v in numeric):
        return ["none" if v is None else str(v) for v in values], "-"
    return [str(i + 1) for i in range(len(values))], "0"


def _slot_set(workflow: Dict, legacy_key: str, value) -> None:
    """Write a registry entry's value where the run keeps it (None = unset)."""
    where, _, key = legacy_key.partition('.')
    bucket = {"advanced": "advanced_options", "mode_config": "mode_config",
              "extra": "extra_options"}.get(where)
    target = workflow if bucket is None else workflow.setdefault(bucket, {})
    if target is None:
        target = workflow[bucket] = {}
    if value is None:
        if bucket is None:
            workflow[key] = None
        else:
            target.pop(key, None)
    else:
        target[key] = value


def _screen_editable(opt: "Opt") -> bool:
    """What the options screen may change: a plain entry with a slot of its
    own. The rules (delete, provenance, conversion type) and the essentials
    (dry run, overwrite/sync, distance, bit depth) stay in the wizard, where
    their gates and questions are."""
    return (opt.kind in ("flag", "value", "notnone", "tri") and bool(opt.legacy_key)
            and opt.level not in ("internal", "essential") and opt.value is None
            and not opt.legacy_key.startswith("advanced.delete"))


def _display_value(opt: "Opt", ctx: "_CmdCtx") -> str:
    v = _opt_value(opt, ctx) if (opt.legacy_key or opt.value) else None
    if opt.kind == "flag":
        return "on" if v else "—"
    if opt.kind == "tri":
        return "on" if v is True else ("off" if v is False else "—")
    if v is None or v == "" or v is False:
        return "—"
    return str(v)


def _extra_options_summary(workflow: Dict) -> List[str]:
    """'--flag value' of every extra option set for this run's direction
    (the Step 7 summary lists them before YES)."""
    origin, dest = workflow.get('origin_format'), workflow.get('dest_format')
    if not origin or not dest:
        return []
    ctx = _CmdCtx(origin, dest, workflow.get('input_dir'), None, workflow.get('mode'),
                  workflow.get('workers'), workflow,
                  workflow.get('advanced_options') or {}, False, None)
    out = []
    for o in REGISTRY:
        if o.legacy_key.startswith("extra.") and _opt_is_on(o, ctx):
            tokens = _emit_opt(o, ctx)
            if tokens:
                out.append(" ".join(tokens))
    return out

# === END OF SCREENS ==========================================================


# === SCRIPT SETTINGS OVERLAY (menu S) ========================================
#
# The four scripts take their settings from the top of each file, and from a
# JSON overlay (JXLPHOTO_SETTINGS / .jxl_script_settings.json, see the block
# "SCRIPT SETTINGS OVERLAY" in each script). The editor below writes that
# file. What can be changed, with which type and range, is each script's own
# _OVERLAY_SETTINGS table (imported, never copied here); the help text is the
# comment under the setting in the script.

_OVERLAY_SCRIPTS = (("jxl_tiff_encoder", "TIFF -> JXL (encoder)"),
                    ("jxl_tiff_decoder", "JXL -> TIFF (decoder)"),
                    ("jxl_jpeg_transcoder", "JPEG <-> JXL, JXL -> JPEG/PNG (transcoder)"),
                    ("jxl_recompressor", "JXL -> JXL (recompressor)"))


def _script_setting_literals(module: str) -> Dict[str, Tuple[Any, str]]:
    """{NAME: (value as the script file has it, help)} of the module-level
    settings, read with `ast` (the overlay never shows up here). A value
    computed from another setting (EXIFTOOL_TIMEOUT = CJXL_TIMEOUT) is
    returned as the text '= CJXL_TIMEOUT'."""
    import ast as _ast
    try:
        src = (SCRIPT_DIR / f"{module}.py").read_text(encoding="utf-8")
        tree = _ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return {}
    lines = src.splitlines()
    out = {}
    for node in tree.body:
        if not (isinstance(node, _ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], _ast.Name)):
            continue
        name = node.targets[0].id
        try:
            value = _ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            value = "= " + _ast.unparse(node.value)
        help_lines = []
        for line in lines[node.end_lineno:node.end_lineno + 3]:
            text = line.strip()
            if not text.startswith("#"):
                break
            help_lines.append(text.lstrip("#").strip())
        out[name] = (value, " ".join(h for h in help_lines if h))
    return out


def _overlay_json(value):
    """A setting's value as the overlay file stores it (a tuple is a list)."""
    return list(value) if isinstance(value, tuple) else value


def _parse_setting_text(spec, text: str):
    """What the user typed for a setting of this spec, as a JSON value, or
    raise ValueError. The script's own _overlay_value checks it afterwards."""
    kind = spec[0]
    t = text.strip()
    if kind.endswith("?") and kind != "name?" and t.lower() in ("none", "null"):
        return None
    if kind == "bool":
        if t.lower() in ("y", "yes", "true", "on", "1"):
            return True
        if t.lower() in ("n", "no", "false", "off", "0"):
            return False
        raise ValueError("answer yes or no")
    if kind in ("int", "int?"):
        return int(t)
    if kind in ("float", "float?"):
        return float(t)
    if kind == "strs":
        return [part.strip() for part in re.split(r"[;,]", t) if part.strip()]
    return t


def _write_overlay_file(path: Path, data: Dict) -> None:
    """The overlay file, written whole through a temp file + os.replace (a
    run starting meanwhile reads the old file or the new one, never half)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".jxl_script_settings_", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

# === END OF SCRIPT SETTINGS OVERLAY ==========================================


# === JOBS ====================================================================
#
# A Job is one run, described by what was CHOSEN for it (task, folder, mode,
# options). Presets are saved as {"schema": 2, "job": {...}} and ALSO carry
# the old last_* snapshot generated from the job: the settings file is shared
# with older copies of the toolkit (an execution copy, the Windows program)
# whose --run-preset reads only the last_* fields. The last job stays in the
# last_* fields for the same reason; ConfigManager.last_job() reads it as a
# Job. Every replay goes through _run_saved_session with the v1 snapshot of
# the job, so a preset replays exactly the command line it did before.

# (id, label, origin, dest, conversion types offered — the first is the default)
TASKS: Tuple[Tuple[str, str, str, str, Tuple[str, ...]], ...] = (
    ("archive_tiff", "Archive TIFFs as JXL masters", "tiff", "jxl",
     ("jxl_tiff_encoder", "jxl_tiff_encoder_lossless")),
    ("restore_tiff", "Restore TIFFs from JXL masters", "jxl", "tiff",
     ("jxl_tiff_decoder",)),
    ("derive_jxl", "Make smaller or resized JXL copies", "jxl", "jxl",
     ("jxl_recompress",)),
    ("export_jpeg", "Export JPEGs for sharing (lossy)", "jxl", "jpeg",
     ("jxl_to_jpeg_force", "jxl_to_jpeg_auto")),
    ("export_png", "Export PNGs", "jxl", "png", ("jxl_to_png",)),
    ("archive_jpeg", "Archive JPEGs losslessly (bit-exact)", "jpeg", "jxl",
     ("transcode_lossless",)),
    ("recover_jpeg", "Get the original JPEGs back (bit-exact)", "jxl", "jpeg",
     ("jxl_to_jpeg_lossless", "jxl_to_jpeg_auto")),
    ("reencode_jpeg", "Re-encode JPEGs lossy", "jpeg", "jxl", ("convert_lossy",)),
)
_TASK_BY_ID = {t[0]: t for t in TASKS}


def _task_of(origin: Optional[str], dest: Optional[str], conv: Optional[str]) -> str:
    """The TASKS id of an (origin, dest, conversion type), or "custom".
    jxl_to_jpeg_auto (lossless when the JXL can rebuild its JPEG, lossy
    otherwise) counts as "recover_jpeg": the old menu offered it first."""
    if conv == "jxl_to_jpeg_auto":
        return "recover_jpeg"
    for tid, _label, o, d, convs in TASKS:
        if o == origin and d == dest and conv in convs:
            return tid
    return "custom"


@dataclass
class Job:
    task: str
    source: Optional[str] = None
    dest: Optional[str] = None
    mode: int = 0
    options: Dict[str, Any] = field(default_factory=dict)
    expert_flags: Optional[str] = None
    manifest_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"task": self.task, "source": self.source, "dest": self.dest,
                "mode": self.mode, "options": dict(self.options),
                "expert_flags": self.expert_flags, "manifest_path": self.manifest_path}

    @classmethod
    def from_dict(cls, d: Any) -> "Job":
        """A Job from its saved form; ValueError on anything else (a hand edit)."""
        if not isinstance(d, dict) or not isinstance(d.get("task"), str):
            raise ValueError("not a job")
        opts = d.get("options") or {}
        if not isinstance(opts, dict):
            raise ValueError("options is not an object")
        mode = d.get("mode", 0)
        if isinstance(mode, bool) or not isinstance(mode, int):
            raise ValueError("mode is not a whole number")
        for key in ("source", "expert_flags"):
            if d.get(key) is not None and not isinstance(d.get(key), str):
                raise ValueError(f"{key} is not text")
        for key in ("dest", "manifest_path"):
            if d.get(key) is not None and not isinstance(d.get(key), str):
                raise ValueError(f"{key} is not text")
        return cls(task=d["task"], source=d.get("source"), dest=d.get("dest"),
                   mode=mode, options=dict(opts), expert_flags=d.get("expert_flags"),
                   manifest_path=d.get("manifest_path"))


# The v1 fields a Job keeps outside `options` (every other last_* goes there).
_JOB_OWN_FIELDS = ("last_input_dir", "last_output_mode", "last_expert_flags",
                   "last_manifest_path")


def job_from_session_v1(session: Dict) -> Job:
    """The Job of a v1 snapshot (the last_* fields of the config, or a v1
    preset). Lossless: session_v1_from_job gives every non-None last_* back."""
    opts = {k[len("last_"):]: v for k, v in session.items()
            if k.startswith("last_") and v is not None and k not in _JOB_OWN_FIELDS}
    mode = _session_int(session, "last_output_mode", 0)
    mc = session.get("last_mode_config") if isinstance(session.get("last_mode_config"), dict) else {}
    # A mode stored as text ("7") is kept as it was, so the way back is exact.
    if session.get("last_output_mode") is not None:
        opts["_output_mode_raw"] = session.get("last_output_mode")
    return Job(task=_task_of(session.get("last_origin_format"),
                             session.get("last_dest_format"),
                             session.get("last_conversion_type")),
               source=session.get("last_input_dir"),
               dest=(mc or {}).get("output_dir") if mode == 2 else None,
               mode=mode, options=opts,
               expert_flags=session.get("last_expert_flags"),
               manifest_path=session.get("last_manifest_path"))


def session_v1_from_job(job: Job) -> Dict:
    """The v1 snapshot (last_* fields) of a Job — what _run_saved_session and
    older copies of the toolkit read."""
    out: Dict[str, Any] = {}
    for k, v in (job.options or {}).items():
        if k.startswith("_") or v is None:
            continue
        out[f"last_{k}"] = v
    if job.source is not None:
        out["last_input_dir"] = job.source
    raw_mode = (job.options or {}).get("_output_mode_raw")
    out["last_output_mode"] = raw_mode if raw_mode is not None else str(job.mode)
    if job.expert_flags is not None:
        out["last_expert_flags"] = job.expert_flags
    if job.manifest_path is not None:
        out["last_manifest_path"] = job.manifest_path
    if job.mode == 2 and job.dest:
        mc = dict(out.get("last_mode_config") or {})
        mc["output_dir"] = job.dest
        out["last_mode_config"] = mc
    if job.task in _TASK_BY_ID and "last_origin_format" not in out:
        _tid, _label, origin, dest, convs = _TASK_BY_ID[job.task]
        out["last_origin_format"], out["last_dest_format"] = origin, dest
        out.setdefault("last_conversion_type", convs[0])
    return out


def _preset_v2(session: Dict) -> Dict:
    """The saved form of a preset: the job, plus the v1 snapshot generated
    from it (older copies of the toolkit read only that)."""
    job = job_from_session_v1(session)
    return {"schema": 2, "job": job.to_dict(), **session_v1_from_job(job)}


def _preset_session(preset: Dict) -> Dict:
    """The v1 snapshot to replay for a saved preset, v1 or v2. A v2 preset's
    job wins over its last_* copy; a job that cannot be read falls back to the
    copy, with a warning (a hand edit)."""
    if not isinstance(preset, dict):
        return {}
    if preset.get("schema") == 2 and "job" in preset:
        try:
            return session_v1_from_job(Job.from_dict(preset["job"]))
        except ValueError as e:
            print(f"WARNING: this preset's job cannot be read ({e}); using its "
                  f"saved settings instead.")
    return {k: v for k, v in preset.items() if k.startswith("last_")}

# === END OF JOBS =============================================================


# === JOB GATES ===============================================================
#
# The refusals made before a run, in ONE ordered table for the three paths
# that start one: "common" (every run, before the manifest dispatch),
# "direct" (a folder run), "manifest" (a CSV; needs the resolved rows) and
# "preset" (an unattended --run-preset). Each rule prints its own message
# and returns True when it refuses; a rule may also only print a note. The
# HHMM and in-place confirmations stay in _confirm_archive_mode /
# _confirm_in_place_replace, asked by the paths after the gates.


def _gate_ambiguous_delete_flags(menu, wf, ctx) -> bool:
    # A delete flag the child's argparse rejects cannot run: it exits 2
    # ("ambiguous option", or an explicit argument on a store_true flag)
    # before the child does anything. Caught ahead of every gate, so the user
    # is not charged an HHMM token for a doomed run.
    _amb = _flags_ambiguous_delete(wf.get('expert_flags'))
    if _amb:
        menu._print_error(
            f"Expert flag {_amb!r} cannot run: the child scripts reject it (it is "
            f"an ambiguous abbreviation of --delete-source / --delete-skipped / "
            f"--delete-confirm-off, or an '=...' value on a flag that takes none). "
            f"Spell the flag out in full, with no value.")
        return True
    return False


def _gate_expert_flags_parse(menu, wf, ctx) -> bool:
    _xprob = _expert_flags_problem(wf.get('origin_format'), wf.get('dest_format'),
                                   wf.get('expert_flags'))
    if _xprob:
        menu._print_error(f"Expert flags cannot run: {_xprob}. Nothing was started; "
                          f"fix or remove them.")
        return True
    return False


def _gate_registry_rules(menu, wf, ctx) -> bool:
    _rule = _registry_rule_problem(wf)
    if _rule:
        menu._print_error(f"These options cannot run together: {_rule}. Nothing was "
                          f"started; change them on the summary's options screen.")
        return True
    return False


def _gate_mode_in_expert_flags(menu, wf, ctx) -> bool:
    _mode_flag = _flags_set_mode(wf.get('expert_flags'))
    if _mode_flag:
        menu._print_error(
            f"Expert flag {_mode_flag!r} cannot run: the mode is chosen in the "
            f"wizard (or the manifest's Mode column), and every check before the "
            f"run — in-place and delete confirmations, collisions, counts — uses "
            f"that one. Remove it from the expert flags and pick the mode there.")
        return True
    return False


def _note(text: str, prefix: str = "") -> None:
    if RICH_AVAILABLE and console:
        console.print(f"[yellow]{_rich_safe(text)}[/yellow]")
    else:
        print(f"{prefix}{text}")


def _gate_direct_derivatives(menu, wf, ctx) -> bool:
    """Derivatives (colour conversion, resize, sharpening) and output
    renaming have no in-place form, and a derivative never deletes; the
    degraded decodes, the lossy decode (T-B) and the lossless recovery's
    rename follow. Refused before the HHMM token is charged for a run the
    child would reject with exit 2."""
    origin, dest = wf['origin_format'], wf['dest_format']
    _adv_pre = wf.get('advanced_options') or {}
    _shaping_pre = _has_derivative_options(_adv_pre)
    _delete_pre = bool(_adv_pre.get('delete_source')
                       or _flags_request_delete(wf.get('expert_flags')))
    _conv_pre = wf.get('conversion_type', '')
    if origin == 'jxl' and dest == 'jxl':
        if _shaping_pre:
            if wf.get('mode') in (0, 8):
                menu._print_error("Derivatives (colour conversion/resize/sharpening) "
                                  "cannot run in place (modes 0/8) — pick mode 1-7.")
                return True
            if _delete_pre:
                menu._print_error("A derivative never deletes its source — drop the "
                                  "delete option.")
                return True
        if _adv_pre.get('rename_from') and wf.get('mode') in (0, 8):
            menu._print_error("--rename-from cannot be used in place (modes 0/8): the "
                              "renamed file would sit beside its source and be "
                              "re-processed as a new input.")
            return True
    if (origin == 'jxl' and dest in ('jpeg', 'png')
            and _conv_pre in ('jxl_to_jpeg_auto', 'jxl_to_jpeg_force', 'jxl_to_png')
            and _shaping_pre and _delete_pre):
        # Same rule for the transcoder's decode direction: a resized or
        # sharpened JPEG/PNG is a derivative, and the child refuses
        # --delete-source with the resize/sharpening flags.
        menu._print_error("Resize/sharpening write a derivative that never deletes "
                          "its source — drop the delete option.")
        return True
    if origin == 'jxl' and dest == 'tiff' and _delete_pre:
        # W4 (2026-10-08 audit): a degraded decode is not a copy of the master.
        if _adv_pre.get('none'):
            menu._print_error("Decode mode 'none' writes no colour profile and no "
                              "XMP/IPTC — the TIFF is not a copy of the master, so "
                              "it cannot delete the JXLs. Drop the delete option "
                              "(or pick another decode mode).")
            return True
        if str(_bit_depth_or_default(wf, 'tiff')) == '8' or _adv_pre.get('basic'):
            _note("Bit depth 8 / decode mode 'basic' makes a degraded copy: the "
                  "decoder KEEPS the JXLs whose master is deeper than 8 bits or "
                  "whose real profile lives in XMP.")
    if (origin == 'jxl' and dest in ('jpeg', 'png')
            and _conv_pre in ('jxl_to_jpeg_auto', 'jxl_to_jpeg_force', 'jxl_to_png')
            and wf.get('icc_profile') and _delete_pre):
        # W6 (2026-10-08 audit): a colour conversion is a derivative too.
        menu._print_error("Colour conversion (Convert to sRGB) writes a derivative "
                          "that never deletes its source — drop the delete option "
                          "(or the conversion).")
        return True
    # T-B: a lossy decode is not a copy of the master (the transcoder exits 2
    # on the same pair).
    _never = _lossy_decode_never_deletes(wf) if _delete_pre else None
    if _never:
        menu._print_error(f"This run cannot delete its sources: {_never}. Drop the "
                          f"delete option.")
        return True
    if _delete_pre and origin == 'jxl' and _conv_pre == 'jxl_to_jpeg_auto':
        _note("Delete with the automatic JXL → JPEG: only JPEG archives "
              "recovered bit for bit delete their JXL; the JXLs converted "
              "lossy are kept.")
    # --rename-from on the lossless JXL->JPEG recovery is impossible: the
    # recovered JPEG keeps the original file name (W2-1a).
    if (origin == 'jxl' and dest in ('jpeg', 'png')
            and _conv_pre == 'jxl_to_jpeg_lossless' and _adv_pre.get('rename_from')):
        menu._print_error("A bit-exact JPEG reconstruction keeps the "
                          "original file names — --rename-from does not "
                          "apply to the lossless JXL→JPEG recovery. To "
                          "rename, use a manifest row with the lossy "
                          "conversion (RenameFrom column).")
        return True
    # OutputICC is ignored on the lossless path: warn, don't refuse.
    if (origin == 'jxl' and dest in ('jpeg', 'png')
            and _conv_pre == 'jxl_to_jpeg_lossless' and _adv_pre.get('output_icc')):
        _note("OutputICC is ignored by a bit-exact JPEG reconstruction "
              "— the recovered JPEG keeps the original bytes, "
              "colour included.", prefix="WARNING: ")
    # A direct JXL -> JPEG/PNG run does not pass --rename-from (S4 of the
    # 261103 review): said out loud instead of dropped in silence.
    if (origin == 'jxl' and dest in ('jpeg', 'png')
            and _conv_pre in _DECODE_LOSSY_CONVERSIONS and _adv_pre.get('rename_from')):
        _note("--rename-from is not passed on a direct JXL → JPEG/PNG run "
              "(the value comes from a JXL → JXL setting or a hand-edited "
              "preset): the outputs keep their names. Use a manifest "
              "RenameFrom column to rename them.", prefix="WARNING: ")
    return False


def _gate_manifest_derivatives(menu, wf, ctx) -> bool:
    """The derivative rules per manifest ROW (a column can make one row a
    derivative where the wizard's answer made none, or vice versa): only the
    rows that combine a derivative with a delete or an in-place layout are
    refused, before anything is charged or written."""
    origin, dest = ctx['origin'], ctx['dest']
    advanced = ctx['advanced']
    row_effs = ctx['row_effs']
    resolved_entries = ctx['resolved_entries']
    _derived_rows = [i + 1 for i, (_w, _a) in enumerate(row_effs)
                     if _has_derivative_options(_a)]
    _rename_rows = [i + 1 for i, (_w, _a) in enumerate(row_effs) if _a.get('rename_from')]
    _manifest_delete = bool(advanced.get('delete_source')
                            or _flags_request_delete(wf.get('expert_flags')))
    if origin == 'jxl' and dest == 'jxl' and (_derived_rows or _rename_rows):
        if _derived_rows and _manifest_delete:
            menu._print_error("A derivative (colour conversion/resize/sharpening) "
                              "never deletes its source — drop the delete option "
                              f"(derivative row(s): {', '.join(map(str, _derived_rows))}).")
            return True
        # resolved_entries, NOT manifest_entries: a legacy row (no Mode cell)
        # arrives as None, which _derivative_in_place_rows never flags.
        _in_place = set()
        for _i, _e in enumerate(resolved_entries):
            if _derivative_in_place_rows([_e]):
                _in_place.add(_i + 1)
        _bad = sorted(_in_place & (set(_derived_rows) | set(_rename_rows)))
        if _bad:
            menu._print_error(f"Derivatives (colour conversion/resize/sharpening) and "
                              f"--rename-from cannot run in place, and manifest "
                              f"row(s) {', '.join(map(str, _bad))} would (mode 8, or "
                              f"mode 0 with Destination = Source). Give them another "
                              f"mode (1-7) or a different Destination:")
            for _n in _bad[:5]:
                s_, d_, m_ = resolved_entries[_n - 1]
                print(f"  row {_n}, mode {m_}: {s_}")
            if len(_bad) > 5:
                print(f"  ... and {len(_bad) - 5} more")
            return True
    if (origin == 'jxl' and dest in ('jpeg', 'png')
            and wf.get('conversion_type') in
                ('jxl_to_jpeg_auto', 'jxl_to_jpeg_force', 'jxl_to_png')
            and _manifest_delete):
        # Resize/sharpening rows with a delete are refused up front (an
        # OutputICC row alone only loses its delete, in _policy_delete).
        _shape_rows = [i + 1 for i, (_w, _a) in enumerate(row_effs)
                       if _a.get('resize_mode') or (_a.get('sharpen') or 'none') != 'none']
        if _shape_rows:
            menu._print_error("Resize/sharpening write a derivative that never deletes "
                              "its source — remove the delete option or move "
                              f"row(s) {', '.join(map(str, _shape_rows))} to a separate "
                              "manifest without it.")
            return True
    return False


def _gate_preset_unattended(menu, wf, ctx) -> bool:
    """A destructive preset cannot run unattended: the delete gates ask for an
    HHMM token nobody can type, and skipping them would let a scheduled task
    delete originals on its own. Three ways a stored run reaches a deletion
    are checked: delete_source in ANY mode, --delete-source in the expert
    flags, and a JXL -> JXL run in modes 0/8 (it replaces the sources in
    place: the recompressor's confirmation would read an EOF stdin)."""
    if ctx.get('dry_run'):
        return False
    session = ctx['session']
    _adv_delete = bool((session.get('last_advanced_options') or {}).get('delete_source'))
    _flag_delete = _flags_request_delete(session.get('last_expert_flags'))
    _in_place_recompress = (ctx['origin'] == 'jxl' and ctx['dest'] == 'jxl'
                            and ctx['mode'] in (0, 8))
    if _flag_delete or _adv_delete or _in_place_recompress:
        why = ("replaces the source JXLs in place"
               if _in_place_recompress and not (_flag_delete or _adv_delete)
               else "deletes source files "
                    f"({'--delete-source in expert flags' if _flag_delete else 'delete_source is on'})")
        menu._print_error(
            f"This preset {why}. "
            "That confirmation cannot be given unattended - run it from "
            "the menu, or add --dry-run to simulate it here.")
        return True
    return False


# (name, paths, rule). Order matters: a path runs its rules top to bottom
# and stops at the first refusal.
_GATE_RULES: Tuple[Tuple[str, frozenset, Callable], ...] = (
    ("ambiguous_delete_flags", frozenset({"common"}), _gate_ambiguous_delete_flags),
    ("expert_flags_parse", frozenset({"common"}), _gate_expert_flags_parse),
    ("registry_rules", frozenset({"common"}), _gate_registry_rules),
    ("mode_in_expert_flags", frozenset({"common"}), _gate_mode_in_expert_flags),
    ("direct_derivatives", frozenset({"direct"}), _gate_direct_derivatives),
    ("manifest_derivatives", frozenset({"manifest"}), _gate_manifest_derivatives),
    ("preset_unattended", frozenset({"preset"}), _gate_preset_unattended),
)


def _job_gates(menu, workflow: Dict, path: str, **ctx) -> bool:
    """Run the _GATE_RULES of `path` in order; True = refused (the rule has
    printed why). Every path that starts a run calls it at the point where
    its checks always ran, so the order of the messages is the old one."""
    for _name, paths, rule in _GATE_RULES:
        if path in paths and rule(menu, workflow, ctx):
            return True
    return False

# === END OF JOB GATES ========================================================


class InteractiveMenu:
    def __init__(self, config_manager: ConfigManager,
                 dependency_checker: DependencyChecker):
        self.config = config_manager
        self.checker = dependency_checker
        # Summary parsed from the last child's ##JXLSUM## line, or None when it
        # produced none (crash, cancellation, older script).
        self._last_child_summary: Optional[Dict] = None
        # argparse's own error line from the last child, or None. See
        # _CHILD_USAGE_ERROR_RE: exit 2 means two very different things.
        self._last_child_usage_error: Optional[str] = None

    def display_status(self, status: Dict[str, bool]) -> None:
        """Display status in single line at top (v3 style)"""
        status_line = self.checker.format_status_line(status)

        if RICH_AVAILABLE and console:
            grid = Table.grid(expand=True)
            grid.add_column()
            grid.add_row(Panel(
                status_line,
                title="[bold blue]JXL Tools Environment[/bold blue]",
                border_style="blue"
            ))
            console.print(grid)
        else:
            print("=" * 60)
            print(f"JXL Tools Environment: {status_line}")
            print("=" * 60)

    def show_main_menu(self, has_last_session: bool) -> str:
        """Display main menu - NO DEFAULT"""
        options = []

        options.append(("", "CONVERT", None))
        if has_last_session:
            last_info = f"({self.config.config.last_output_mode or 'unknown'})"
            options.append(("1", "New job: archive, restore, export, recompress...", True))
            # A manifest workflow is repeatable as long as its CSV is still
            # there — the repeat re-reads the file. Offering an entry whose CSV
            # was deleted or moved would only dead-end at the confirmation, so
            # it is disabled with the reason spelled out instead.
            # Hand-edited JSON can store the NUMBER 99 (or "99.0" from Excel);
            # a string-only == sent those down the non-manifest branch, where
            # the repeat of a manifest run was offered as a plain mode-99
            # workflow. Coerce exactly like _session_int does.
            _raw_last_mode = self.config.config.last_output_mode
            try:
                _last_is_manifest = (_raw_last_mode not in (None, "")
                                     and _as_exact_int(_raw_last_mode) == 99)
            except (TypeError, ValueError):
                # A corrupt mode is refused at run time by _session_number_error;
                # here it only means "not a manifest" for labelling purposes.
                _last_is_manifest = False
            if _last_is_manifest:
                saved_manifest = self.config.config.last_manifest_path
                if saved_manifest and Path(saved_manifest).exists():
                    options.append(("2", f"Repeat last job (manifest: {Path(saved_manifest).name})", True))
                else:
                    options.append(("2", "Repeat last job [manifest CSV no longer available]", False))
            else:
                options.append(("2", f"Repeat last job {last_info}", True))
        else:
            options.append(("1", "New job: archive, restore, export, recompress...", True))
            options.append(("2", "Repeat last job (none saved)", False))

        preset_count = len(self.config.config.presets or {})
        preset_label = (f"Presets ({preset_count} saved)" if preset_count
                        else "Presets (save a job under a name)")
        options.extend([
            ("3", preset_label, True),
            ("4", "Folder list (manifest): run a CSV of folders", True),
            ("", "MAINTAIN", None),
            ("5", "Renamed or moved folders: fix the masters' path records", True),
            ("6", "Explorer thumbnails for existing JXLs (jxl-thumb)", True),
            ("7", "JPEG archives (jbrd): audit, repair, EXIF for IrfanView", True),
            ("8", "Reset the encoder's ICC test cache", True),
            ("", "SETTINGS", None),
            ("9", "Settings: wrapper defaults, script settings, reset, move", True),
            ("D", "Dependencies: check again", True),
        ])
        if getattr(self, '_update_available', None):
            options.append(("U", f"Update available: {self._update_available[0]} — "
                                 f"open the release page", True))
        options.extend([
            ("0", "Exit", True),
        ])
        # The letters menus R/C/S had in v2.22.0_beta1 still work (not shown).
        aliases = {"R": "5", "C": "8", "S": "S"}

        if RICH_AVAILABLE and console:
            table = Table(show_header=False, box=None)
            table.add_column("Key", style="bold cyan")
            table.add_column("Option")
            table.add_column("Status", justify="center")

            for key, desc, available in options:
                if available is None:
                    table.add_row("", f"[bold]{desc}[/bold]", "")
                    continue
                status_str = "" if available else "[dim](unavailable)[/dim]"
                table.add_row(key, desc, status_str)

            console.print(Panel(table, title="Main Menu", border_style="green"))

            valid_choices = [o[0] for o in options if o[2]]
            while True:
                # D (and the old R/C/S) are letters: either case is taken
                # (Rich's own choices= check would refuse a lower-case 'd').
                choice = Prompt.ask("Select option").strip().upper()
                choice = aliases.get(choice, choice)
                if choice in valid_choices or choice == "S":
                    return choice
                console.print(f"[red]Choose one of: {', '.join(valid_choices)}[/red]")
        else:
            print("\n--- Main Menu ---")
            for key, desc, available in options:
                if available is None:
                    print(desc)
                    continue
                status_str = "" if available else " [UNAVAILABLE]"
                print(f"[{key}] {desc}{status_str}")

            valid_choices = [o[0] for o in options if o[2]]
            while True:
                choice = input("\nSelect option: ").strip().upper()
                choice = aliases.get(choice, choice)
                if choice in valid_choices or choice == "S":
                    return choice
                print(f"Invalid choice. Valid options: {', '.join(valid_choices)}")

    @property
    def ui(self) -> "UI":
        """The question API of the registry screens (tests set `_ui`)."""
        ui = self.__dict__.get('_ui')
        if ui is None:
            ui = RichUI() if (RICH_AVAILABLE and console) else PlainUI()
            self.__dict__['_ui'] = ui
        return ui

    def _all_options_screen(self, workflow: Dict, flt: str = "") -> None:
        """Step 7's "all options for this job": every registry entry of this
        run's direction, grouped, with its value; the plain ones can be
        changed here (written to their slot, so the command builder and the
        saved session see them), the rules stay in the wizard. `/text`
        filters; Enter goes back to the summary."""
        ui = self.ui
        origin, dest = workflow.get('origin_format'), workflow.get('dest_format')
        if not origin or not dest:
            return
        workflow.setdefault('extra_options', {})
        while True:
            ctx = _CmdCtx(origin, dest, workflow.get('input_dir'), None,
                          workflow.get('mode'), workflow.get('workers'), workflow,
                          workflow.get('advanced_options') or {}, False,
                          self.config.config.export_marker)
            rows = []
            for o in REGISTRY:
                if ctx.direction not in o.directions or o.level == "internal":
                    continue
                if o.when is not None and not o.when(ctx):
                    continue
                if flt and flt not in f"{o.label} {o.flag} {o.help}".lower():
                    continue
                rows.append(o)
            ui.section(f"All options — {origin.upper()} -> {dest.upper()}"
                       + (f" (filter: {flt})" if flt else ""))
            numbered = {}
            for gid, title in GROUPS:
                mine = [o for o in rows if o.group == gid]
                if not mine:
                    continue
                ui.line(title)
                for o in mine:
                    n = str(len(numbered) + 1)
                    numbered[n] = o
                    tag = "" if _screen_editable(o) else "  (set in the wizard)"
                    mark = " (!)" if o.danger else ""
                    ui.line(f"  [{n:>2}] {o.label}{mark}: {_display_value(o, ctx)}"
                            f"   {o.flag}{tag}")
            answer = ui.ask_text("opt.screen", "Number to change, /text to filter, "
                                 "Enter to go back", "").strip()
            if not answer:
                return
            if answer.startswith("/"):
                flt = answer[1:].strip().lower()
                continue
            o = numbered.get(answer)
            if o is None:
                ui.warn(f"No option {answer!r}.")
                continue
            if not _screen_editable(o):
                ui.warn(f"'{o.label}' is set in the wizard's own steps.")
                continue
            before = _opt_value(o, ctx)
            new = self._ask_option_value(o, before)
            if new is _KEEP:
                continue
            if o.danger and new is not None and new is not False and not ui.confirm(
                    "opt.danger", f"'{o.label}' relaxes a safety check. Turn it on?",
                    False):
                continue
            _slot_set(workflow, o.legacy_key, new)
            problem = _registry_rule_problem(workflow)
            if problem:
                _slot_set(workflow, o.legacy_key, before)
                ui.warn(f"Not changed: {problem}.")

    def _ask_option_value(self, o: "Opt", current):
        """The new value of one entry (None = unset), or _KEEP."""
        ui = self.ui
        qid = f"opt.{o.id}"
        ui.line(f"{o.label} ({o.flag}): {o.help}")
        if o.kind == "flag":
            return None if current else True
        if o.kind == "tri":
            key = ui.choose(qid, "Value", [("1", "script default"), ("2", "on"),
                                           ("3", "off")], "1")
            return {"1": None, "2": True, "3": False}[key]
        if o.choices:
            keys, dflt = _choice_keys(o.choices)
            opts = [(dflt, "script default")] + [(k, str(c))
                                                 for k, c in zip(keys, o.choices)]
            key = ui.choose(qid, "Value", opts, dflt)
            return None if key == dflt else o.choices[keys.index(key)]
        text = ui.ask_text(qid, "Value (empty = script default)",
                           "" if current in (None, False) else str(current)).strip()
        if not text:
            return None
        if o.kind == "notnone":
            try:
                return float(text)
            except ValueError:
                ui.warn(f"{text!r} is not a number.")
                return _KEEP
        return text

    def restamp_flow(self, status) -> None:
        """Menu R: `jxl_tiff_encoder.py <folder> --mode N --restamp-moved`.

        After a folder of TIFFs was renamed or moved, the masters' path record
        names the old place, and the overwrite guard refuses to re-encode
        them. The restamp rewrites the record of every master whose TIFF is
        unchanged, without re-encoding (dates kept). Dry run first."""
        ui = self.ui
        script = SCRIPT_DIR / 'jxl_tiff_encoder.py'
        if not script.exists():
            self._print_error(f"Script not found: {script}")
            return
        if not status.get('exiftool'):
            self._print_error("exiftool is required.")
            return
        ui.section("Renamed or moved folders: fix the masters' path records")
        ui.line("Each master records the path of the TIFF it was made from. After a "
                "folder was renamed or moved, this rewrites that record for every "
                "master whose TIFF is unchanged — nothing is re-encoded, file dates "
                "are kept. Masters whose TIFF changed since are listed and left alone.")
        cfg = self.config.config
        default_dir = cfg.last_input_dir or str(Path.home())
        folder = _strip_surrounding_quotes(
            ui.ask_text("restamp.folder", "TIFF folder (the renamed one, or above it)",
                        default_dir))
        if not folder or not Path(folder).is_dir():
            self._print_error(f"Not a folder: {folder}")
            return
        last_mode = cfg.last_output_mode if cfg.last_origin_format == 'tiff' else None
        mode = ui.choose("restamp.mode", "Mode the masters were written with",
                         [(str(m), name) for m, name in (
                             (0, "next to the TIFFs, this folder only (or an output folder)"),
                             (1, "a subfolder next to the TIFFs, this folder only"),
                             (2, "one flat output folder"),
                             (3, "a subfolder inside each TIFF folder"),
                             (4, "a renamed copy of each TIFF folder"),
                             (5, "a sibling folder of each TIFF folder"),
                             (6, "below the export marker"),
                             (7, "below the export marker, one subfolder"),
                             (8, "next to the TIFFs, every subfolder"))],
                         last_mode if last_mode in [str(m) for m in range(9)] else "8")
        cmd = [sys.executable, str(script), folder]
        if mode in ("0", "2"):
            out = _strip_surrounding_quotes(ui.ask_text(
                "restamp.output", "Folder the masters are in (empty = the same folder)",
                ""))
            if out:
                cmd.append(out)
        cmd += ["--mode", mode, "--export-marker",
                cfg.export_marker or ToolConfig.export_marker]
        if mode == "7":
            sub = ui.ask_text("restamp.subfolder",
                              "Export subfolder (empty = the script setting)", "").strip()
            if sub:
                cmd += ["--export-subfolder", sub]
        if mode in ("6", "7"):
            jf = ui.ask_text("restamp.jxl_folder",
                             "Masters' folder below the marker (empty = the script "
                             "setting)", "").strip()
            if jf:
                cmd += ["--export-jxl-folder", jf]
        cmd.append("--restamp-moved")
        dry = ui.confirm("restamp.dry_run", "Dry run first (shows what would change)?",
                         True)
        rc = self._stream_child(cmd + (["--dry-run"] if dry else []))
        if dry and rc == 0 and ui.confirm("restamp.run", "Rewrite those records now?",
                                          False):
            self._stream_child(cmd)

    def clear_icc_cache_flow(self, status) -> None:
        """Menu C: `jxl_tiff_encoder.py --clear-icc-cache`. The cautious ICC
        strategy remembers, per profile and cjxl version, whether a profile
        survives a lossy encode; clearing makes the next lossy run test each
        profile again (after a libjxl update, or to re-check a profile)."""
        ui = self.ui
        script = SCRIPT_DIR / 'jxl_tiff_encoder.py'
        if not script.exists():
            self._print_error(f"Script not found: {script}")
            return
        ui.section("Reset the ICC test cache")
        ui.line("The encoder's cautious ICC strategy remembers which colour profiles "
                "survive a lossy encode (per cjxl version). Clearing it makes the next "
                "lossy run test each profile again. Nothing else is touched.")
        if ui.confirm("icccache.confirm", "Clear the cache now?", False):
            self._stream_child([sys.executable, str(script), "--clear-icc-cache"])

    def script_settings_flow(self) -> None:
        """Menu S: the four scripts' settings, changed in the overlay file
        instead of the .py files (the scripts check every value again when
        they start, and never take a safety setting from it)."""
        ui = self.ui
        ui.section("Script settings (the scripts' own defaults, without editing them)")
        try:
            probe = importlib.import_module(_OVERLAY_SCRIPTS[0][0])
            path = probe._settings_overlay_path()
        except Exception as e:
            self._print_error(f"The scripts could not be loaded ({e}).")
            return
        if path is None:
            ui.line("JXLPHOTO_SETTINGS is set and empty: the overlay is OFF for every "
                    "run started from here. Unset it to use one.")
            return
        ui.line(f"File: {path}")
        ui.line("Every script run uses a change at once; the wizard's own defaults "
                "show it from the next start of this program. Deletion, "
                "confirmations, guards and verification stay per-run choices.")
        while True:
            key = ui.choose("settings.script", "Script (0 = back)",
                            [("0", "back")] + [(str(i + 1), label) for i, (_m, label)
                                               in enumerate(_OVERLAY_SCRIPTS)], "0")
            if key == "0":
                return
            module = _OVERLAY_SCRIPTS[int(key) - 1][0]
            try:
                mod = importlib.import_module(module)
            except Exception as e:
                self._print_error(f"{module} could not be loaded ({e}).")
                continue
            self._edit_script_settings(mod, module, path)

    def _edit_script_settings(self, mod, module: str, path: Path) -> None:
        ui = self.ui
        specs = mod._OVERLAY_SETTINGS
        literals = _script_setting_literals(module)
        flt = ""
        while True:
            data, problem = {}, None
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
                if not isinstance(data, dict):
                    problem, data = "it is not a JSON object", {}
            except (OSError, ValueError) as e:
                problem = str(e)
            if problem:
                ui.warn(f"{path} cannot be read ({problem}). Fix or delete it first; "
                        f"the scripts ignore it meanwhile.")
                return
            section = data.get(module) if isinstance(data.get(module), dict) else {}
            ui.section(f"{module}" + (f" (filter: {flt})" if flt else ""))
            numbered = {}
            for name in specs:
                script_value, help_text = literals.get(name, (getattr(mod, name, None), ""))
                if flt and flt not in f"{name} {help_text}".lower():
                    continue
                n = str(len(numbered) + 1)
                numbered[n] = name
                if name in section:
                    ok, _v = mod._overlay_value(specs[name], section[name])
                    shown = (f"{json.dumps(section[name], ensure_ascii=False)}  (overlay"
                             + ("" if ok else ", REFUSED by the script") + ")")
                else:
                    shown = f"{json.dumps(_overlay_json(script_value), ensure_ascii=False)}"
                ui.line(f"  [{n:>2}] {name} = {shown}")
            answer = ui.ask_text("settings.pick", "Number to change, /text to filter, "
                                 "Enter to go back", "").strip()
            if not answer:
                return
            if answer.startswith("/"):
                flt = answer[1:].strip().lower()
                continue
            name = numbered.get(answer)
            if name is None:
                ui.warn(f"No setting {answer!r}.")
                continue
            spec = specs[name]
            script_value, help_text = literals.get(name, (getattr(mod, name, None), ""))
            if help_text:
                ui.line(f"{name}: {help_text}")
            ui.line(f"In the script: {json.dumps(_overlay_json(script_value), ensure_ascii=False)}")
            if spec[0] == "choice":
                values = list(spec[1:])
                keys, dflt = _choice_keys(values)
                opts = [(dflt, "the script's value (remove from the overlay)")] + [
                    (k, json.dumps(c)) for k, c in zip(keys, values)]
                pick = ui.choose("settings.choice", "Value", opts, dflt)
                remove = pick == dflt
                new = _KEEP if remove else values[keys.index(pick)]
            else:
                text = ui.ask_text("settings.value", "New value ('-' = the script's "
                                   "value, Enter = keep)", "")
                if not text.strip():
                    continue
                remove = text.strip() == "-"
                new = _KEEP
                if not remove:
                    try:
                        new = _parse_setting_text(spec, text)
                    except ValueError as e:
                        ui.warn(f"Not changed: {e}.")
                        continue
                    ok, checked = mod._overlay_value(spec, new)
                    if not ok:
                        ui.warn(f"Not changed: {name} must be {checked}.")
                        continue
                    new = _overlay_json(checked)
            sect = dict(section)
            if remove:
                sect.pop(name, None)
            else:
                sect[name] = new
            if sect:
                data[module] = sect
            else:
                data.pop(module, None)
            try:
                _write_overlay_file(path, data)
            except OSError as e:
                self._print_error(f"Could not write {path}: {e}")
                return
            ui.line(f"{name}: " + ("back to the script's value" if remove
                                   else f"{json.dumps(new, ensure_ascii=False)} (overlay)"))

    def repair_jbrd_flow(self, status) -> None:
        """Menu 7 (JPEG archives): audit/repair jbrd JXLs whose JPEG recovery broke.

        Thin wrapper over `jxl_jpeg_transcoder.py <folder> --repair-jbrd`: the
        repair itself already works on a copy and only replaces a file when
        the copy provably reconstructs, but the cautious route (a copy of the
        folder) is said out loud before anything runs.
        """
        script = SCRIPT_DIR / 'jxl_jpeg_transcoder.py'
        if not script.exists():
            self._print_error(f"Script not found: {script}")
            return
        if not (status.get('djxl') and status.get('exiftool')):
            self._print_error("djxl and exiftool are required for the repair audit.")
            return

        default_dir = self.config.config.last_input_dir or str(Path.home())
        if RICH_AVAILABLE and console:
            console.print("\n[bold cyan]Repair JPEG recovery (jbrd)[/bold cyan]")
            console.print(
                "[dim]v2.0.0-v2.0.3 wrote XMP markers into lossless JPEG->JXL files, "
                "which can make djxl --reconstruct_jpeg fail for sources that already "
                "had XMP. This scans every JXL under a folder and repairs the broken "
                "ones. It also rewrites the EXIF of JPEG->JXL files written before "
                "v2.19.2 so IrfanView shows it (image data untouched). Every repair "
                "works on a COPY and only replaces a file when the copy provably "
                "rebuilds the same JPEG — the cautious route is still to run it on a "
                "copy of the folder first.[/dim]")
            # Explorer's "Copy as path" wraps the folder in double quotes;
            # every other wizard prompt strips them (_strip_surrounding_quotes)
            # and this one used to hand them to Path().is_dir() raw — "Folder
            # not found" for a folder that exists.
            folder = _strip_surrounding_quotes(
                Prompt.ask("Folder to scan", default=default_dir))
            audit = Confirm.ask("Audit only (dry run, nothing written)?", default=True)
        else:
            print("\n--- Repair JPEG recovery (jbrd) ---")
            print("v2.0.0-v2.0.3 wrote XMP markers into lossless JPEG->JXL files,")
            print("which can make djxl --reconstruct_jpeg fail for sources that")
            print("already had XMP. It also rewrites the EXIF of JPEG->JXL files")
            print("written before v2.19.2 so IrfanView shows it (image data untouched).")
            print("Every repair works on a COPY and only replaces a file when the copy")
            print("provably rebuilds the same JPEG — the cautious route is still to")
            print("run it on a copy of the folder first.")
            folder = _strip_surrounding_quotes(
                input(f"Folder to scan [{default_dir}]: ").strip() or default_dir)
            audit_input = input("Audit only (dry run, nothing written)? [Y/n]: ").strip().lower()
            audit = not audit_input.startswith('n')

        if not Path(folder).is_dir():
            self._print_error(f"Folder not found: {folder}")
            return

        cmd = [sys.executable, str(script), folder, '--repair-jbrd']
        if audit:
            cmd.append('--dry-run')
        rc = self._stream_child(cmd)
        if rc != 0:
            self._print_error("Repair reported files still broken — see the log above.")

    def explorer_thumbnail_flow(self, status) -> None:
        """Menu 6: Explorer thumbnails for JXLs that already exist.

        Thin wrapper over `jxl_recompressor.py <folder> --refresh-explorer-thumbnails`
        (mode 0 = this folder, mode 8 = with subfolders). Nothing is
        re-encoded; each file is edited through a verified copy and keeps its
        modified date, so a later sync still sees a re-exported TIFF as newer.
        """
        script = SCRIPT_DIR / 'jxl_recompressor.py'
        if not script.exists():
            self._print_error(f"Script not found: {script}")
            return
        if not (status.get('djxl') and status.get('exiftool') and status.get('magick')):
            self._print_error("djxl, exiftool and ImageMagick (magick) are required.")
            return
        policies = {"1": "auto", "2": "on", "3": "off"}
        default_dir = self.config.config.last_input_dir or str(Path.home())
        if RICH_AVAILABLE and console:
            console.print("\n[bold cyan]Explorer thumbnails for existing JXLs[/bold cyan]")
            console.print(
                "[dim]A small sRGB JPEG stored inside each file, which jxl-thumb shows "
                "in Windows Explorer without decoding the photo (most useful for "
                "lossless files). Nothing is re-encoded; each file keeps its "
                "modified date. JPEG->JXL lossless files (jbrd) are never touched. "
                "It also moves the EXIF where IrfanView reads it, in files written "
                "by v2.17.0 to v2.19.0.[/dim]")
            folder = _strip_surrounding_quotes(Prompt.ask("Folder", default=default_dir))
            recursive = Confirm.ask("Include subfolders?", default=True)
            console.print("[1] automatic (recommended)   [2] every file   "
                          "[3] remove them")
            policy = policies[Prompt.ask("Which files", choices=["1", "2", "3"], default="1")]
            dry = Confirm.ask("Dry run (only list what would change)?", default=False)
        else:
            print("\n--- Explorer thumbnails for existing JXLs ---")
            print("A small sRGB JPEG stored inside each file, which jxl-thumb shows in")
            print("Windows Explorer without decoding the photo. Nothing is re-encoded;")
            print("each file keeps its modified date. jbrd files are never touched.")
            print("It also moves the EXIF where IrfanView reads it, in files written")
            print("by v2.17.0 to v2.19.0.")
            folder = _strip_surrounding_quotes(
                input(f"Folder [{default_dir}]: ").strip() or default_dir)
            recursive = not input("Include subfolders? [Y/n]: ").strip().lower().startswith('n')
            print("[1] automatic (recommended)  [2] every file  "
                  "[3] remove them")
            policy = policies.get(input("Which files [1]: ").strip() or "1", "auto")
            dry = input("Dry run (only list what would change)? [y/N]: ").strip().lower().startswith('y')

        if not Path(folder).is_dir():
            self._print_error(f"Folder not found: {folder}")
            return
        cmd = [sys.executable, str(script), folder, '--mode', '8' if recursive else '0',
               '--refresh-explorer-thumbnails', '--explorer-thumbnail', policy]
        if dry:
            cmd.append('--dry-run')
        rc = self._stream_child(cmd)
        if rc != 0:
            self._print_error("Some files could not be updated — see the log above.")

    def edit_settings(self) -> None:
        current = self.config.config
        # Snapshot BEFORE any prompt: `current` IS self.config.config, so a
        # comparison made after the write-back would always compare equal.
        # Used below to propagate only the values the user actually changed.
        was_workers, was_quality = current.default_workers, current.default_quality
        was_effort = current.default_effort
        was_distance = _sane_distance(current.default_distance)
        # Asked only where the tip means something: no thumbnail handler on
        # this PC, or the tip already hidden (so it can be turned back on).
        ask_tip = (not current.show_thumbnail_tip) or _jxl_thumbnail_tip() is not None

        def _effective(default_value, last_value) -> str:
            """Flag a default that the saved session is currently overriding —
            otherwise editing it here looks like it did nothing, because the
            wizard and 'Repeat last workflow' both read the last_* value."""
            if last_value is None or last_value == default_value:
                return f"{default_value:g}" if isinstance(default_value, float) else f"{default_value}"
            shown = f"{default_value:g}" if isinstance(default_value, float) else f"{default_value}"
            was = f"{last_value:g}" if isinstance(last_value, float) else f"{last_value}"
            return f"{shown}  (last run used {was} — editing this adopts the new value)"

        if RICH_AVAILABLE and console:
            console.print("[bold]Current Settings:[/bold]")
            console.print(f"Staging: {_rich_safe(current.staging_dir or 'system default')}")
            console.print(f"Workers: {_effective(current.default_workers, current.last_workers)}")
            console.print(f"Quality: {_effective(current.default_quality, current.last_quality)}")
            console.print(f"Effort: {_effective(current.default_effort, current.last_effort)}")
            console.print("Distance (TIFF->JXL): "
                          f"{_effective(_sane_distance(current.default_distance), current.last_distance)}")
            console.print(f"Confirm deletes: {current.confirm_delete}")
            console.print(f"Export marker: {_rich_safe(current.export_marker)}")

            new_staging = Prompt.ask("Staging dir (empty=keep current, 'none'=system default)", default=current.staging_dir or "")
            new_workers = IntPrompt.ask("Workers", default=current.default_workers)
            new_quality = IntPrompt.ask("Quality (1-100)", default=current.default_quality)
            new_effort = IntPrompt.ask("Effort (1-10)", default=current.default_effort)
            new_distance = Prompt.ask(
                "Distance for TIFF->JXL (0=lossless, 0.1=near-lossless)",
                default=f"{_sane_distance(current.default_distance):g}")
            new_confirm = Confirm.ask("Confirm before delete?", default=current.confirm_delete)
            new_marker = Prompt.ask("Export marker", default=current.export_marker)
            new_tip = (Confirm.ask("Show the Explorer thumbnail tip at start?",
                                   default=current.show_thumbnail_tip)
                       if ask_tip else current.show_thumbnail_tip)
        else:
            print("\n--- Edit Settings ---")
            print(f"Current staging: {current.staging_dir or 'system default'}")
            new_staging = input("New staging (empty=keep, 'none'=system default): ").strip()

            print(f"Current workers: {_effective(current.default_workers, current.last_workers)}")
            workers_input = input("New workers: ").strip()
            new_workers = int(workers_input) if workers_input.isdigit() else current.default_workers

            print(f"Current quality: {_effective(current.default_quality, current.last_quality)}")
            quality_input = input("New quality (1-100): ").strip()
            new_quality = int(quality_input) if quality_input.isdigit() else current.default_quality

            print(f"Current effort: {_effective(current.default_effort, current.last_effort)}")
            effort_input = input("New effort (1-10): ").strip()
            new_effort = int(effort_input) if effort_input.isdigit() else current.default_effort

            print("Current distance (TIFF->JXL): "
                  f"{_effective(_sane_distance(current.default_distance), current.last_distance)}")
            new_distance = input("New distance (0=lossless, 0.1=near-lossless): ").strip()

            confirm_input = input("Confirm before delete? (y/n): ").strip().lower()
            new_confirm = confirm_input.startswith('y') if confirm_input else current.confirm_delete

            print(f"Current marker: {current.export_marker}")
            new_marker = input("New export marker: ").strip() or current.export_marker

            new_tip = current.show_thumbnail_tip
            if ask_tip:
                tip_input = input("Show the Explorer thumbnail tip at start? (y/n): ").strip().lower()
                if tip_input:
                    new_tip = tip_input.startswith('y')

        if new_staging.lower() == 'none':
            self.config.config.staging_dir = None
        elif new_staging:
            self.config.config.staging_dir = new_staging

        self.config.config.export_marker = new_marker or ToolConfig.export_marker
        self.config.config.default_workers = max(1, min(new_workers, 32))
        self.config.config.default_quality = max(1, min(new_quality, 100))
        self.config.config.default_effort = max(1, min(new_effort, 10))
        # Empty = keep current (the non-rich branch offers no default). A typed
        # value that does not parse is reported instead of silently ignored:
        # the whole point of this setting is that the number shown in Step 2 is
        # the number the user asked for.
        typed_distance = (new_distance or "").strip()
        if typed_distance:
            current_distance = _sane_distance(current.default_distance)
            try:
                self.config.config.default_distance = _sane_distance(
                    float(typed_distance), fallback=current_distance)
            except ValueError:
                self._print_error(f"Invalid distance {typed_distance!r} — keeping {current_distance:g}")
                self.config.config.default_distance = current_distance
        self.config.config.confirm_delete = new_confirm
        self.config.config.show_thumbnail_tip = bool(new_tip)

        # Changing a default here is a statement about the NEXT run, so it also
        # adopts the saved session's value. Without this the wizard and 'Repeat
        # last workflow' keep reading last_* from an older session and the whole
        # settings screen appears to do nothing (the "repeat resets my workers"
        # complaint). Only values the user actually changed are propagated —
        # merely opening the screen and pressing Enter must not overwrite what
        # the last run used.
        cfg = self.config.config
        if cfg.default_workers != was_workers:
            cfg.last_workers = cfg.default_workers
        if cfg.default_quality != was_quality:
            cfg.last_quality = cfg.default_quality
        if cfg.default_effort != was_effort:
            cfg.last_effort = cfg.default_effort
        if _sane_distance(cfg.default_distance) != was_distance:
            cfg.last_distance = _sane_distance(cfg.default_distance)

        self.config.save_config()
        self._print_success("Settings saved!")

    # === NEW JOB (delivery C): task, folder, mode, quality, review =========

    @staticmethod
    def _task_unavailable(origin: str, dest: str, conv: str,
                          status: Dict[str, bool]) -> Optional[str]:
        """What a task needs and this PC lacks, or None (the old Steps 1-2:
        JPEG/TIFF -> JXL need cjxl, TIFF needs tifffile, JXL needs djxl, a
        JXL -> JXL recompression needs cjxl too)."""
        need = []
        if origin in ('jpeg', 'tiff') or dest == 'jxl':
            need.append('cjxl')
        if origin == 'jxl':
            need.append('djxl')
        if 'tiff' in (origin, dest):
            need.append('tifffile')
        missing = [n for n in need if not status.get(n)]
        return ", ".join(missing) if missing else None

    def _new_workflow(self) -> Dict[str, Any]:
        """A run's starting state: the last job's values, else the wrapper's
        settings, else the children's (the old wizard's defaults)."""
        c = self.config.config
        last_staging = c.last_staging if c.last_staging is not None else c.staging_dir
        wf = {
            'input_dir': None, 'origin_format': None, 'dest_format': None,
            'conversion_type': None, 'mode': None,
            'workers': c.last_workers or c.default_workers,
            'quality': c.last_quality or c.default_quality,
            'effort': c.last_effort or c.default_effort,
            'staging': last_staging, 'icc_profile': None,
            'use_ram': (c.last_use_ram if c.last_use_ram is not None
                        else _child_default('jxl_tiff_encoder', 'use_ram')),
            'compression': c.last_compression or _child_default('jxl_tiff_decoder', 'compression'),
            'bit_depth': c.last_bit_depth,
            'add_preview': (c.last_add_preview if c.last_add_preview is not None
                            else _child_default('jxl_tiff_decoder', 'add_preview')),
            'dry_run': False, 'advanced_options': {}, 'expert_flags': '',
            'mode_config': {}, 'auto_mode_used': False,
        }
        wf['_from'] = {
            'workers': 'last job' if c.last_workers else 'settings',
            'effort': 'last job' if c.last_effort else 'settings',
            'quality': 'last job' if c.last_quality else 'settings',
            'staging': 'last job' if c.last_staging is not None else 'settings',
            'use_ram': 'last job' if c.last_use_ram is not None else 'script',
            'compression': 'last job' if c.last_compression else 'script',
            'bit_depth': 'last job' if c.last_bit_depth else 'script',
            'add_preview': 'last job' if c.last_add_preview is not None else 'script',
            'overwrite_guard': 'last job' if c.last_overwrite_guard is not None else 'script',
            'd50_patch': 'script',
        }
        return wf

    def _job_pick_task(self, status: Dict[str, bool]) -> Optional[str]:
        """'job.task': what the run does. Each task is the (source, output,
        conversion) the old Steps 1-2 picked."""
        ui = self.ui
        ui.section("New job — what do you want to do?")
        keys = {}
        options = []
        for i, (tid, label, origin, dest, convs) in enumerate(TASKS, 1):
            why = self._task_unavailable(origin, dest, convs[0], status)
            options.append((str(i), f"{label:42} {origin.upper()} → {dest.upper()}"
                            + (f"   (needs {why})" if why else "")))
            keys[str(i)] = (tid, why)
        options.append(("0", "back"))
        while True:
            key = ui.choose("job.task", "Task", options, "1")
            if key == "0":
                return None
            tid, why = keys[key]
            if why:
                ui.warn(f"This task needs {why}, which this PC does not have.")
                continue
            return tid

    def _ask_number(self, qid: str, prompt: str, default, lo, hi, kind=float):
        """A number from self.ui, clamped to [lo, hi]; the default on a typo."""
        text = self.ui.ask_text(qid, prompt, f"{default:g}" if isinstance(default, float)
                                else str(default))
        try:
            value = kind(text)
        except (TypeError, ValueError):
            self.ui.warn(f"Not a number: {text!r} — keeping {default}.")
            return default
        return max(lo, min(value, hi))

    def _job_quality(self, workflow: Dict) -> bool:
        """'job.quality': the one quality question a task has, asked before the
        review (the review can change it again). Archive TIFFs: lossless /
        your default distance / visually lossless / custom; JXL -> JXL and
        JPEG -> JXL lossy: a distance; JPEG export: a quality."""
        ui = self.ui
        tid = workflow.get('_task')
        frm = workflow.setdefault('_from', {})
        if tid == 'archive_tiff':
            preferred = _sane_distance(self.config.config.default_distance)
            opts = [("1", "Lossless (d=0, an exact replica)"),
                    ("2", f"d={preferred:g} — "
                          + ("near-lossless (recommended)" if preferred == 0.1
                             else "your default (change it in Settings)")),
                    ("3", "d=1.0 — visually lossless"),
                    ("4", "Custom distance")]
            key = ui.choose("job.quality", "Quality", opts, "2")
            if key == "1":
                workflow['distance'] = 0.0
            elif key == "2":
                workflow['distance'] = preferred
            elif key == "3":
                workflow['distance'] = 1.0
            else:
                last = self.config.config.last_distance
                start = _sane_distance(last, fallback=preferred) if last is not None else preferred
                workflow['distance'] = self._ask_number("job.distance", "Distance (0-15, lower = better)",
                                                        start, 0.0, 15.0)
            workflow['conversion_type'] = ('jxl_tiff_encoder_lossless'
                                           if workflow['distance'] == 0.0 and key != "4"
                                           else 'jxl_tiff_encoder')
            frm['distance'] = 'you'
            _sd = _child_scan_distance()
            try:
                _differs = _sd != "same" and float(_sd) != float(workflow['distance'])
            except (TypeError, ValueError):
                _differs = False
            if _differs:
                ui.line(f"  Film scans use distance {_sd}"
                        f"{' (lossless)' if float(_sd) == 0 else ''} — scanner software, an IR "
                        f"page, or a TIFF with no Software tag and no camera EXIF (the run lists "
                        f"those folders). The review's options change it.")
        elif tid == 'derive_jxl':
            workflow['distance'] = self._ask_number(
                "job.distance", "Target distance (0 = lossless, 1 = visually lossless, 2 = smaller)",
                workflow.get('distance', _child_default('jxl_recompressor', 'distance')), 0.0, 15.0)
            frm['distance'] = 'you'
        elif tid == 'reencode_jpeg':
            workflow['distance'] = self._ask_number(
                "job.distance", "Distance (0 = lossless, 0.1 = near-lossless, 1 = visually lossless)",
                workflow.get('distance', 0.5), 0.0, 15.0)
            frm['distance'] = 'you'
        elif tid == 'export_jpeg':
            workflow['quality'] = int(self._ask_number(
                "job.jpeg_quality", "JPEG quality (1-100)",
                _workflow_or_child(workflow, 'quality', 'jxl_jpeg_transcoder'), 1, 100, int))
            frm['quality'] = 'you'
        return True

    def _review_init(self, workflow: Dict, status: Dict[str, bool]) -> None:
        """The state the old Steps 6/6A left when every question was answered
        with Enter, so a run reviewed and started with [r] runs exactly what
        the old wizard ran by default. The review's rows and the options
        screen then change it in place."""
        conv = workflow['conversion_type']
        origin, dest = workflow['origin_format'], workflow['dest_format']
        adv = workflow.setdefault('advanced_options', {})
        delete_on = _step6_delete_on(workflow)
        # Step 6, all defaults taken.
        if conv == 'jxl_recompress':
            workflow.setdefault('distance', _child_default('jxl_recompressor', 'distance'))
            adv['on_downgrade'] = 'copy'
            adv['on_regeneration'] = 'convert'
            if workflow.get('distance') and float(workflow['distance']) > 0:
                adv['on_scan'] = 'copy'
            # Enter on the old colour question was "keep" and on resize/
            # sharpen "none": both CLEARED the recipe, so an answer from an
            # earlier pass never rode along (and none is offered in place or
            # with [D]). The review offers it again from scratch.
            _drop_derivative_options(adv)
        elif conv == 'convert_lossy':
            workflow.setdefault('distance', 0.5)
        if (origin == 'jxl' and dest == 'jpeg'
                and conv in ('jxl_to_jpeg_auto', 'jxl_to_jpeg_force')):
            workflow['quality'] = _workflow_or_child(workflow, 'quality', 'jxl_jpeg_transcoder')
        if (origin == 'jxl' and dest in ('jpeg', 'png')
                and conv in ('jxl_to_jpeg_auto', 'jxl_to_jpeg_force', 'jxl_to_png')):
            if delete_on:
                workflow.pop('icc_profile', None)
                _drop_derivative_options(adv)
            elif not status.get('magick'):
                _drop_derivative_options(adv)
            else:
                # Enter on the old resize/sharpen questions: "none" (cleared).
                for _k in ('resize_mode', 'resize_value', 'allow_upscale', 'sharpen'):
                    adv.pop(_k, None)
        if dest == 'png':
            workflow['bit_depth'] = int(_bit_depth_or_default(workflow, 'png'))
        if dest == 'tiff':
            workflow['bit_depth'] = int(_bit_depth_or_default(workflow, 'tiff'))
            if origin == 'jxl':
                workflow['decode_mode_asked'] = 'roundtrip'
        workflow['dry_run'] = False
        workflow['overwrite_mode'] = '2'
        if not delete_on:
            _og_child = _child_default(_child_module(origin, dest), 'overwrite_guard')
            _og_last = self.config.config.last_overwrite_guard
            _og = _og_child if _og_last is None else bool(_og_last)
            workflow['overwrite_guard'] = bool(_og)
            workflow.setdefault('_pending_session_fields', {})['last_overwrite_guard'] = bool(_og)
        if origin == 'tiff' and dest == 'jxl':
            workflow['d50_patch'] = _child_default('jxl_tiff_encoder', 'd50_patch')
        # Step 6A, "Configure advanced options? n".
        self._advanced_defaults(workflow)

    def _advanced_defaults(self, workflow: Dict) -> None:
        """The advanced options of a run whose advanced questions were not
        asked (the old Step 6A "no"): rebuilt from the run's own answers, the
        last job and the children's settings. Step 6's recompressor policies
        and derivative recipe are carried across (losing them drops the child
        back to `ask`, a SILENT SKIP on the wrapper's pipe)."""
        origin, dest = workflow['origin_format'], workflow['dest_format']
        c = self.config.config
        _prev_adv = workflow.get('advanced_options') or {}
        advanced_options: Dict[str, Any] = {}
        if workflow.get('overwrite_mode', '2') == "1":
            advanced_options['overwrite'], advanced_options['sync'] = True, False
        else:
            advanced_options['overwrite'], advanced_options['sync'] = False, True
        if origin == 'tiff' and dest == 'jxl':
            advanced_options['d50_patch'] = (workflow.get('d50_patch')
                                             or _child_default('jxl_tiff_encoder', 'd50_patch'))
            advanced_options['encode_tag'] = (workflow.get('encode_tag')
                                              or _child_default('jxl_tiff_encoder', 'encode_tag'))
            advanced_options['multipage_mode'] = (c.last_multipage_mode
                                                  or _child_default('jxl_tiff_encoder', 'multipage_mode'))
            advanced_options['thumbnail_mode'] = (c.last_thumbnail_mode
                                                  or _child_default('jxl_tiff_encoder', 'thumbnail_mode'))
            advanced_options['thumbnail_suffix'] = (c.last_thumbnail_suffix
                                                    or _child_default('jxl_tiff_encoder', 'thumbnail_suffix'))
        elif origin == 'jxl' and dest == 'tiff':
            advanced_options['thumbnail_handling'] = (c.last_thumbnail_handling
                                                      or _child_default('jxl_tiff_decoder', 'thumbnail_handling'))
            advanced_options['thumbnail_suffix'] = (c.last_thumbnail_suffix
                                                    or _child_default('jxl_tiff_decoder', 'thumbnail_suffix'))
            _no_recon = c.last_no_reconstruct_multipage
            advanced_options['no_reconstruct_multipage'] = (
                bool(_no_recon) if _no_recon is not None
                else not _child_default('jxl_tiff_decoder', 'reconstruct_multipage'))
            advanced_options['depth_policy'] = (c.last_depth_policy
                                                or _child_default('jxl_tiff_decoder', 'depth_policy'))
        for key in ('matrix', 'basic', 'none', 'target_icc'):
            if key in _prev_adv:
                advanced_options[key] = _prev_adv[key]
        # The delete decision (and its gates) taken in Step 4's [D].
        if workflow.get('delete_source'):
            advanced_options['delete_source'] = True
        if workflow.get('verify_roundtrip'):
            advanced_options['verify_roundtrip'] = True
        if workflow.get('delete_skipped'):
            advanced_options['delete_skipped'] = True
        if workflow.get('provenance'):
            advanced_options['provenance'] = workflow['provenance']
        if workflow.get('overwrite_guard') is False:
            advanced_options['overwrite_guard'] = False
        if workflow.get('adopt_scan') is False:
            advanced_options['adopt_scan'] = False
        for _k in ('on_downgrade', 'on_regeneration', 'on_unknown', 'on_scan',
                   'jbrd_policy', 'no_keep_smaller',
                   'rederive_on_encode_change', 'rederive_on_lower_effort',
                   'output_icc', 'rename_from', 'rename_to',
                   'resize_mode', 'resize_value', 'allow_upscale', 'sharpen'):
            if _k in _prev_adv:
                advanced_options[_k] = _prev_adv[_k]
        workflow['advanced_options'] = advanced_options

    def _review_rows(self, workflow: Dict, status: Dict[str, bool]) -> List[Tuple[str, str, str, Callable]]:
        """(key, label, value shown, editor) of the run's essential options —
        the questions the old Step 6 asked, each with its own rule (what is
        offered with [D] or in place). The editors write the same keys the old
        questions wrote."""
        ui = self.ui
        conv = workflow['conversion_type']
        origin, dest = workflow['origin_format'], workflow['dest_format']
        adv = workflow.setdefault('advanced_options', {})
        frm = workflow.setdefault('_from', {})
        delete_on = _step6_delete_on(workflow)
        rows: List[Tuple[str, str, str, Callable]] = []

        def yn(v):
            return "yes" if v else "no"

        def you(key):
            frm[key] = 'you'

        _conv_labels = {
            'jxl_to_jpeg_force': "lossy (quality, colour, size)",
            'jxl_to_jpeg_auto': "auto: bit-exact when the JXL holds a JPEG, else lossy",
            'jxl_to_jpeg_lossless': "bit-exact only (JPEG archives)"}
        _task = _TASK_BY_ID.get(workflow.get('_task'))
        if _task and len(_task[4]) > 1 and origin == 'jxl' and dest == 'jpeg':
            def _conversion(convs=_task[4]):
                key = ui.choose("review.conversion", "Conversion",
                                [(str(i), _conv_labels[c]) for i, c in enumerate(convs, 1)],
                                str(convs.index(workflow['conversion_type']) + 1))
                workflow['conversion_type'] = convs[int(key) - 1]
                # The lossy paths take a quality; the bit-exact one does not.
                if workflow['conversion_type'] in ('jxl_to_jpeg_auto', 'jxl_to_jpeg_force'):
                    workflow['quality'] = _workflow_or_child(workflow, 'quality',
                                                             'jxl_jpeg_transcoder')
                you('conversion')
            rows.append(("conversion", "Conversion",
                         _conv_labels.get(workflow['conversion_type'], workflow['conversion_type']),
                         _conversion))

        if origin == 'tiff':
            def _ram():
                workflow['use_ram'] = ui.confirm("review.use_ram", "Use RAM for the intermediate PNG? (faster)",
                                                 bool(workflow.get('use_ram')))
                you('use_ram')
            rows.append(("use_ram", "PNG in RAM", yn(workflow.get('use_ram')), _ram))

        def _workers():
            workflow['workers'] = int(self._ask_number("review.workers", "Workers (1-32)",
                                                       workflow['workers'], 1, 32, int))
            you('workers')
        rows.append(("workers", "Workers", str(workflow['workers']), _workers))

        if conv in ('jxl_tiff_encoder', 'jxl_tiff_encoder_lossless', 'jxl_recompress', 'convert_lossy'):
            # Set by the quality step or _review_init; the child's own value
            # otherwise, never a literal (AGENTS.md).
            if workflow.get('distance') is None:
                workflow['distance'] = _workflow_or_child(workflow, 'distance',
                                                          _child_module(origin, dest))
            def _distance():
                workflow['distance'] = self._ask_number(
                    "review.distance", "Distance (0 = lossless, 0.1 = near-lossless, "
                    "1 = visually lossless)", workflow['distance'], 0.0, 15.0)
                if origin == 'tiff':
                    workflow['conversion_type'] = ('jxl_tiff_encoder_lossless'
                                                   if workflow['distance'] == 0.0
                                                   else 'jxl_tiff_encoder')
                if conv == 'jxl_recompress':
                    if workflow['distance'] > 0:
                        adv.setdefault('on_scan', 'copy')
                    else:
                        adv.pop('on_scan', None)
                you('distance')
            rows.append(("distance", "Distance", f"{float(workflow['distance']):g}", _distance))
        if not (origin == 'jxl' and dest == 'tiff'):
            def _effort():
                workflow['effort'] = int(self._ask_number("review.effort", "Effort (1-10, higher = smaller)",
                                                          workflow['effort'], 1, 10, int))
                you('effort')
            rows.append(("effort", "Effort", str(workflow['effort']), _effort))

        if conv == 'jxl_recompress':
            for key, label, text in (
                    ("on_downgrade", "Already good enough",
                     "If a file's recorded settings are already as good as or better than this "
                     "target (re-encoding cannot gain anything)"),
                    ("on_regeneration", "Recompressed before",
                     "If a file was already lossy-recompressed once (generation >= 2) and this "
                     "run would add another generation (each adds ~0.2-0.6 dB of loss)"),
                    ("on_scan", "Film scans",
                     "If a file is a film scan (a lossy re-encode loses what only a new scan "
                     "brings back)")):
                if key == "on_scan" and not (workflow.get('distance') and float(workflow['distance']) > 0):
                    continue
                def _policy(key=key, text=text):
                    ui.line(text)
                    adv[key] = ui.choose(f"review.{key}", "copy the original / skip it / convert anyway",
                                         [("copy", "copy the original"), ("skip", "skip it"),
                                          ("convert", "convert anyway")], adv.get(key, 'copy'))
                    you(key)
                rows.append((key, label, str(adv.get(key)), _policy))
            if workflow['mode'] in (0, 8):
                rows.append(("derivative", "Colour/size derivative",
                             "not in place (modes 0/8)", None))
            elif delete_on:
                rows.append(("derivative", "Colour/size derivative", "not with [D]", None))
            else:
                def _colour():
                    target = ui.ask_text("review.output_icc", "Output colour space: keep / sRGB / "
                                         "AdobeRGB / path to .icc", adv.get('output_icc') or "keep").strip()
                    if target and target.lower() != "keep":
                        adv['output_icc'] = target
                        rf = ui.ask_text("review.rename_from", "Rename text in the output file names "
                                         "(e.g. ProPhoto) — empty = keep names",
                                         adv.get('rename_from') or "").strip()
                        if rf:
                            adv['rename_from'] = rf
                            adv['rename_to'] = ui.ask_text("review.rename_to", "Replace it with",
                                                           adv.get('rename_to') or "").strip()
                        else:
                            adv.pop('rename_from', None)
                            adv.pop('rename_to', None)
                    else:
                        for _k in ('output_icc', 'rename_from', 'rename_to'):
                            adv.pop(_k, None)
                    you('output_icc')
                shown = adv.get('output_icc') or "keep"
                if adv.get('rename_from'):
                    shown += f" (rename {adv['rename_from']} → {adv.get('rename_to') or ''})"
                rows.append(("output_icc", "Output colour space", shown, _colour))
                rows.append(("shaping", "Resize / sharpen", _shaping_text(adv),
                             lambda: (_ask_output_shaping(workflow, ui), you('shaping'))))

        def _staging():
            cur = workflow.get('staging') or ""
            text = ui.ask_text("review.staging", "Staging folder ('none' = system default)", cur).strip()
            if text.lower() in ('none', 'system default'):
                workflow['staging'] = None
            elif text:
                workflow['staging'] = _strip_surrounding_quotes(text)
            you('staging')
        rows.append(("staging", "Staging folder", workflow.get('staging') or "system default", _staging))

        if origin == 'jxl' and dest == 'jpeg' and conv in ('jxl_to_jpeg_auto', 'jxl_to_jpeg_force'):
            def _quality():
                workflow['quality'] = int(self._ask_number("review.quality", "JPEG quality (1-100)",
                                                           workflow['quality'], 1, 100, int))
                you('quality')
            rows.append(("quality", "JPEG quality", str(workflow.get('quality')), _quality))
        if (origin == 'jxl' and dest in ('jpeg', 'png')
                and conv in ('jxl_to_jpeg_auto', 'jxl_to_jpeg_force', 'jxl_to_png')):
            if delete_on:
                rows.append(("derivative", "sRGB / resize / sharpen", "not with [D]", None))
            elif status.get('magick'):
                def _srgb():
                    if ui.confirm("review.srgb", "Convert to sRGB?", bool(workflow.get('icc_profile'))):
                        workflow['icc_profile'] = 'sRGB'
                    else:
                        workflow['icc_profile'] = None
                    you('icc_profile')
                rows.append(("icc_profile", "Convert to sRGB", yn(workflow.get('icc_profile')), _srgb))
                rows.append(("shaping", "Resize / sharpen", _shaping_text(adv),
                             lambda: (_ask_output_shaping(workflow, ui), you('shaping'))))
        if dest == 'png':
            def _png_depth():
                workflow['bit_depth'] = int(ui.choose("review.png_depth", "PNG bit depth",
                                                      [("8", "8 bits"), ("16", "16 bits")],
                                                      str(workflow.get('bit_depth') or 16)))
                you('bit_depth')
            rows.append(("bit_depth", "PNG bit depth", str(workflow.get('bit_depth')), _png_depth))
        if dest == 'tiff':
            def _compression():
                workflow['compression'] = ui.choose(
                    "review.compression", "TIFF compression",
                    [("zip", "zip"), ("lzw", "lzw"), ("none", "none")], workflow['compression'])
                you('compression')
            rows.append(("compression", "TIFF compression", str(workflow['compression']), _compression))

            def _tiff_depth():
                workflow['bit_depth'] = int(ui.choose("review.tiff_depth", "Bit depth",
                                                      [("8", "8 bits"), ("16", "16 bits")],
                                                      str(workflow.get('bit_depth') or 16)))
                you('bit_depth')
            rows.append(("bit_depth", "Bit depth", str(workflow.get('bit_depth')), _tiff_depth))

            def _preview():
                workflow['add_preview'] = ui.confirm("review.preview", "Add a JPEG preview? "
                                                     "(faster viewing)", bool(workflow.get('add_preview')))
                you('add_preview')
            rows.append(("add_preview", "JPEG preview", yn(workflow.get('add_preview')), _preview))
            if origin == 'jxl':
                def _decode_mode():
                    mode = ui.choose("review.decode_mode", "Decode mode",
                                     [("roundtrip", "roundtrip (the original profile)"),
                                      ("basic", "basic"), ("matrix", "matrix (+ target profile)"),
                                      ("none", "none")], workflow.get('decode_mode_asked') or 'roundtrip')
                    workflow['decode_mode_asked'] = mode
                    for _k in ('matrix', 'basic', 'none', 'target_icc'):
                        adv.pop(_k, None)
                    if mode == "matrix":
                        adv['matrix'] = True
                        t = ui.ask_text("review.target_icc", "Target ICC (file path or sRGB, "
                                        "empty = none)", "").strip()
                        if t:
                            adv['target_icc'] = t
                    elif mode in ("basic", "none"):
                        adv[mode] = True
                    you('decode_mode')
                rows.append(("decode_mode", "Decode mode",
                             workflow.get('decode_mode_asked') or 'roundtrip', _decode_mode))

        def _existing():
            key = ui.choose("review.existing", "Existing outputs",
                            [("2", "sync — reconvert only what is newer"), ("1", "overwrite all")],
                            workflow.get('overwrite_mode', '2'))
            workflow['overwrite_mode'] = key
            adv['overwrite'], adv['sync'] = (key == "1"), (key != "1")
            you('overwrite_mode')
        rows.append(("existing", "Existing outputs",
                     "overwrite all" if workflow.get('overwrite_mode') == "1" else "sync (only newer)",
                     _existing))

        if delete_on:
            rows.append(("delete", "Delete the sources", "YES — chosen with [D] in the mode step", None))
        else:
            def _guard():
                ui.line("Overwrite guard: an output records the full path of the file that made "
                        "it. On = an output made from ANOTHER path is never overwritten (protects "
                        "photos whose source an earlier run deleted). Off = overwrite it (logged); "
                        "choose off if you never delete sources and you rename or move folders.")
                og = ui.confirm("review.overwrite_guard", "Overwrite guard on?",
                                bool(workflow.get('overwrite_guard', True)))
                workflow['overwrite_guard'] = bool(og)
                workflow.setdefault('_pending_session_fields', {})['last_overwrite_guard'] = bool(og)
                if og:
                    adv.pop('overwrite_guard', None)
                else:
                    adv['overwrite_guard'] = False
                you('overwrite_guard')
            rows.append(("delete", "Delete the sources", "no ([b] back to the mode step for [D])", None))
            rows.append(("overwrite_guard", "Overwrite guard",
                         "on" if workflow.get('overwrite_guard', True) else "off", _guard))
        if origin == 'tiff' and dest == 'jxl':
            def _d50():
                v = ui.choose("review.d50", "D50 patch", [("auto", "auto"), ("on", "on"), ("off", "off")],
                              workflow.get('d50_patch') or 'auto')
                workflow['d50_patch'] = v
                adv['d50_patch'] = v
                you('d50_patch')
            rows.append(("d50_patch", "D50 patch", str(workflow.get('d50_patch')), _d50))
        return rows

    def _summary_info(self, workflow: Dict) -> List[str]:
        """The run's state worth reading before it starts (the old Step 7's
        "Config:" line): staging, delete scope and gates, guard, options,
        expert flags, exclusions, dry run."""
        info = []
        if workflow.get('use_ram') and workflow.get('origin_format') == 'tiff':
            info.append("RAM: Yes")
        if workflow.get('icc_profile'):
            info.append(f"ICC: {workflow['icc_profile']}")
        if workflow.get('auto_mode_used'):
            info.append("Auto Mode: Yes")
        _adv = workflow.get('advanced_options') or {}
        if _adv.get('delete_source'):
            if workflow.get('mode') == 99:
                info.append("DELETE SOURCE: ON (!)")
            else:
                _n_now = self._count_origin_files(workflow, workflow.get('mode'))
                _o = (workflow.get('origin_format') or '').upper()
                _cnt = (f"{_n_now} {_o} file(s) in scope" if _n_now >= 0
                        else f"an unknown number of {_o} file(s) in scope")
                _prev = getattr(self, '_delete_panel_count', None)
                if (_prev is not None and _prev >= 0 and _n_now >= 0 and _prev != _n_now):
                    _cnt += f" (the [D] panel counted {_prev} before the mode settings were known)"
                info.append(f"DELETE SOURCE: ON (!) — {_cnt}")
            info.append("Verify round-trip: ON" if _adv.get('verify_roundtrip')
                        else "Verify round-trip: off (structural check only)")
            if _adv.get('delete_skipped'):
                info.append("Also deletes ALREADY-CONVERTED originals (!)")
            if _adv.get('provenance'):
                info.append(f"Match existing outputs by: {_adv['provenance']}")
            if _adv.get('adopt_scan') is False:
                info.append("Adopt scan: OFF (pairing trusted, not verified)")
        if _adv.get('overwrite_guard') is False:
            info.append("Overwrite guard: off")
        _extras = _extra_options_summary(workflow)
        if _extras:
            info.append("Options: " + ", ".join(_extras))
        if workflow.get('expert_flags'):
            info.append(f"Expert flags: {workflow['expert_flags']} (applied LAST)")
        if workflow.get('exclude_folders'):
            info.append(f"Excluding: {workflow['exclude_folders']}")
        return info

    _MODE_NAMES = {0: "in place", 1: "subfolder", 2: "flat output folder",
                   3: "recursive subfolders", 4: "renamed copy of each folder",
                   5: "sibling folder", 6: "below the export marker",
                   7: "below the export marker, one subfolder", 8: "in place, recursive",
                   99: "manifest (each row its own mode)"}

    def _review_command(self, workflow: Dict) -> Optional[List]:
        """The command the run would start (the direct run), or None."""
        origin, dest = workflow['origin_format'], workflow['dest_format']
        if workflow.get('mode') == 99:
            return None
        script = str(SCRIPT_DIR / f"{_child_module(origin, dest)}.py")
        output_dir = ((workflow.get('mode_config') or {}).get('output_dir')
                      if workflow.get('mode') == 2 else None)
        return self.build_child_cmd(script, origin, dest, workflow['input_dir'], output_dir,
                                    workflow['mode'], workflow['workers'], workflow,
                                    workflow.get('advanced_options') or {}, manifest=False)

    def _review_screen(self, workflow: Dict, status: Dict[str, bool]) -> str:
        """The review of a job: every essential option with where its value
        came from, [a]/[/] all options, [c] the command line, [x] expert
        flags, [d] a dry run (then back here), [r] run, [s] save as a preset,
        [b] back. Returns "ran", "failed", "saved", "back" or "quit"."""
        ui = self.ui
        tid = workflow.get('_task')
        title = _TASK_BY_ID[tid][1] if tid in _TASK_BY_ID else (
            f"{(workflow.get('origin_format') or '?').upper()} → "
            f"{(workflow.get('dest_format') or '?').upper()}")
        while True:
            rows = self._review_rows(workflow, status)
            frm = workflow.get('_from') or {}
            ui.section(f"Review — {title}")
            if workflow.get('mode') == 99:
                ui.line(f"Manifest  {workflow.get('manifest_path')}  "
                        f"({len(workflow.get('manifest_entries') or [])} entries)")
            else:
                ui.line(f"Folder    {workflow.get('input_dir')}")
            ui.line(f"Mode      {workflow.get('mode')} — "
                    f"{self._MODE_NAMES.get(workflow.get('mode'), '?')}")
            if (workflow.get('origin_format'), workflow.get('dest_format')) == ('tiff', 'jxl'):
                _mp = self._multipage_summary(workflow)
                if _mp:
                    ui.line(f"Multi-page TIFFs: {_mp[0]}")
            numbered = {}
            for key, label, value, editor in rows:
                n = str(len(numbered) + 1)
                numbered[n] = (key, label, editor)
                src = frm.get(key, "")
                tag = "" if editor else "   (fixed)"
                ui.line(f"  [{n:>2}] {label:24} {value:34} {src}{tag}")
            for line in self._summary_info(workflow):
                ui.line(f"  · {line}")
            ui.line("[number] change  [a] all options  [/text] search  [c] command line  "
                    "[x] expert flags")
            ui.line("[d] dry run  [r] RUN  [s] save as preset  [b] back  [q] cancel")
            answer = ui.ask_text("review.action", "Review", "").strip()
            low = answer.lower()
            if low in ("", "?"):
                continue
            if low == "q":
                return "quit"
            if low == "b":
                return "back"
            if low == "a":
                self._all_options_screen(workflow)
                continue
            if answer.startswith("/"):
                self._all_options_screen(workflow, flt=answer[1:].strip().lower())
                continue
            if low == "c":
                cmd = self._review_command(workflow)
                if cmd is None:
                    ui.line("A manifest builds one command per row; the run prints each one.")
                else:
                    ui.line(" ".join(_display_cmd(cmd)))
                continue
            if low == "x":
                flags = ui.ask_text("review.expert", "Expert flags (applied last; empty = none)",
                                    workflow.get('expert_flags') or "").strip()
                problem = _expert_flags_problem(workflow['origin_format'],
                                                workflow['dest_format'], flags)
                if problem:
                    ui.warn(f"Not changed: {problem}.")
                else:
                    workflow['expert_flags'] = flags
                continue
            if low == "s":
                raw = ui.ask_text("review.preset_name", "Preset name", "")
                name = self._clean_preset_name(raw)
                if not name:
                    ui.warn("Invalid name (empty, too long, or contains control characters).")
                    continue
                if name in (self.config.config.presets or {}) and not ui.confirm(
                        "review.preset_overwrite", f"Preset '{name}' exists. Overwrite?", False):
                    continue
                self._save_last_from_workflow(workflow)
                self.config.save_preset(name, self.config.session_snapshot())
                self._print_success(f"Preset '{name}' saved (also the last job).")
                continue
            if low in ("d", "r"):
                self._save_last_from_workflow(workflow)
                workflow['dry_run'] = (low == "d")
                try:
                    ok = bool(self.execute_workflow(workflow, status))
                finally:
                    dry = workflow['dry_run']
                    workflow['dry_run'] = False
                if dry:
                    continue
                return "ran" if ok else "failed"
            item = numbered.get(answer)
            if item is None:
                ui.warn(f"No option {answer!r}.")
                continue
            key, label, editor = item
            if editor is None:
                ui.warn(f"'{label}' cannot be changed here.")
                continue
            editor()

    def _save_last_from_workflow(self, workflow: Dict) -> None:
        """Store this run as the last job (the old wizard did it before
        "Execute now?"): the delete-gate answers staged on the workflow are
        committed, and every last_* field the run defines is written."""
        config = self.config
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        conv = workflow['conversion_type']
        if origin == 'tiff' and dest == 'jxl':
            saved_distance = _workflow_or_child(workflow, 'distance', 'jxl_tiff_encoder')
            saved_quality = None
        elif 'lossy' in conv or conv in ('jxl_to_jpeg_force', 'jxl_to_jpeg_auto'):
            saved_quality = _workflow_or_child(workflow, 'quality', 'jxl_jpeg_transcoder')
            saved_distance = workflow.get('distance')
        elif conv == 'jxl_recompress':
            saved_distance = (workflow.get('distance') if workflow.get('distance') is not None
                              else _child_default('jxl_recompressor', 'distance'))
            saved_quality = None
        elif conv == 'jxl_tiff_decoder':
            saved_quality = None
            saved_distance = None
        else:
            saved_quality = config.config.default_quality
            saved_distance = None
        for _field, _value in (workflow.pop('_pending_session_fields', None) or {}).items():
            setattr(config.config, _field, _value)
        adv = workflow.get('advanced_options', {}) or {}
        config.save_last_session(
            input_dir=workflow['input_dir'], output_mode=str(workflow['mode']),
            workers=workflow['workers'], staging=workflow['staging'],
            effort=workflow['effort'], quality=saved_quality, distance=saved_distance,
            origin_format=origin, dest_format=dest, conversion_type=conv,
            icc_profile=workflow.get('icc_profile'), expert_flags=workflow.get('expert_flags'),
            d50_patch=adv.get('d50_patch'), encode_tag=adv.get('encode_tag'),
            multipage_mode=adv.get('multipage_mode'), thumbnail_mode=adv.get('thumbnail_mode'),
            thumbnail_suffix=adv.get('thumbnail_suffix'),
            thumbnail_handling=adv.get('thumbnail_handling'),
            no_reconstruct_multipage=adv.get('no_reconstruct_multipage'),
            depth_policy=adv.get('depth_policy'), advanced_options=adv,
            use_ram=workflow.get('use_ram') if origin == 'tiff' else None,
            compression=workflow.get('compression') if dest == 'tiff' else None,
            bit_depth=workflow.get('bit_depth') if dest in ('tiff', 'png') else None,
            add_preview=workflow.get('add_preview') if dest == 'tiff' else None,
            mode_config=workflow.get('mode_config'),
            manifest_path=workflow.get('manifest_path'),
            extra_options=workflow.get('extra_options') or {},
            exclude_folders=(workflow['exclude_folders']
                             if (origin, dest) in (('tiff', 'jxl'), ('jxl', 'tiff'))
                             and 'exclude_folders' in workflow
                             else config._STAGING_UNSET),
        )

    def new_job_flow(self, status: Dict[str, bool], manifest: bool = False) -> bool:
        """Menu 1 (and 4 with manifest=True): the task, the folder (or the
        manifest), the mode with its [A]/[M]/[D], the quality, then the
        review. [b] on the review goes back to the mode step."""
        import copy
        tid = self._job_pick_task(status)
        if not tid:
            return False
        _tid, _label, origin, dest, convs = _TASK_BY_ID[tid]
        workflow = self._new_workflow()
        workflow.update(origin_format=origin, dest_format=dest,
                        conversion_type=convs[0], _task=tid)
        if manifest:
            workflow['input_dir'] = self.config.config.last_input_dir or os.getcwd()
        elif not self._wizard_select_files(workflow):
            return False
        base = copy.deepcopy(workflow)
        while True:
            workflow = copy.deepcopy(base)
            if manifest:
                if not self._wizard_run_from_manifest(workflow):
                    return False
            elif not self._wizard_select_mode(workflow):
                return False
            if not self._wizard_mode_specific_config(workflow):
                return False
            if not self._job_quality(workflow):
                return False
            self._review_init(workflow, status)
            result = self._review_screen(workflow, status)
            if result == "back":
                continue
            return result == "ran"

    def _wizard_select_files(self, workflow: Dict) -> bool:
        """The folder ('job.folder') and, for TIFF <-> JXL, the folder names to
        exclude ('job.exclude')."""
        ui = self.ui
        default_dir = self.config.config.last_input_dir or os.getcwd()
        ui.section("Folder")
        input_dir = ui.ask_text("job.folder", "Directory containing files", default_dir)
        path = Path(_strip_surrounding_quotes(input_dir or default_dir)).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            self._print_error(f"Invalid directory: {path}")
            return False

        workflow['input_dir'] = str(path)

        # Folder exclusions, ONLY for the directions whose child has the
        # flag (--exclude-folders exists in the TIFF encoder and decoder).
        # Asked here, BEFORE the mode step: its [D] delete gate counts the
        # source files (_count_origin_files), so a later question would leave
        # the "About to delete originals" panel counting a tree the run would
        # skip (bug #297's class). The mode is not known yet, so the note says
        # "recursive modes".
        if (workflow.get('origin_format'), workflow.get('dest_format')) in (
                ('tiff', 'jxl'), ('jxl', 'tiff')):
            _excl_default = self.config.config.last_exclude_folders
            # Enter keeps the displayed default (the last run's exclusion);
            # '-'/'none' mean "no exclusion".
            _excl_raw = ui.ask_text(
                "job.exclude", "Exclude folders? (';'-separated folder names, '-' = none; "
                "only affects recursive modes)", _excl_default or "-")
            _excl_raw = (_excl_raw or "").strip()
            # Staged in the workflow ONLY: persistence happens when the run
            # starts, so cancelling later must not leave this answer on disk
            # (the #319f invariant — a cancelled job persists nothing).
            if not _excl_raw or _excl_raw.lower() in ("-", "none"):
                workflow['exclude_folders'] = None
            else:
                workflow['exclude_folders'] = _excl_raw

        return True

    def _wizard_auto_mode(self, workflow: Dict) -> bool:
        """Auto Mode: analyze folder and recommend mode."""
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        input_dir = Path(workflow['input_dir'])
        export_marker = self.config.config.export_marker

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Auto Mode: Analyzing folder structure...[/bold cyan]")
            console.print("[dim]This may take a moment for large folders...[/dim]")
        else:
            print("\n--- Auto Mode: Analyzing folder structure... ---")
            print("(This may take a moment for large folders...)")

        analyzer = FolderAnalyzer(input_dir, origin, dest, export_marker)
        try:
            analysis = analyzer.analyze()
        except Exception as e:
            self._print_error(f"Auto Mode analysis failed: {e}")
            logger.error(f"Auto Mode analysis failed: {e}")
            return False

        # Print analysis report
        report = analyzer.format_report(analysis)
        if RICH_AVAILABLE and console:
            console.print(report)
        else:
            print(report)

        if analysis['recommended_mode'] is None:
            self._print_error("Could not analyze folder structure.")
            return False

        rec_mode = analysis['recommended_mode']
        confidence = analysis['confidence']

        # Compute folder mappings for the recommended mode
        mappings = analyzer.compute_folder_mappings(analysis, rec_mode)

        # Build recommendation options
        mode_names = {
            0: "In-place", 1: "Subfolder", 2: "Flat", 3: "Recursive subfolders",
            4: "Rename (suffix)", 5: "Sibling", 6: f"Export (full)", 7: f"Export (specific subfolder)"
        }

        rec_name = mode_names.get(rec_mode, f"Mode {rec_mode}")

        if RICH_AVAILABLE and console:
            confidence_label = {
                'high': '[green](high confidence)[/green]',
                'medium': '[yellow](medium confidence)[/yellow]',
                'low': '[dim](low confidence — verify)[/dim]'
            }.get(confidence, '')

            console.print(f"Auto Mode recommends: [bold cyan]Mode {rec_mode} — {rec_name}[/bold cyan] {confidence_label}")
            console.print()

            # Show folder preview
            if mappings:
                console.print("[bold]Folder preview:[/bold]")
                for src, dst, count in mappings[:10]:
                    rel_src = Path(src).relative_to(input_dir) if _is_relative_to(Path(src), input_dir) else Path(src)
                    rel_dst = Path(dst).relative_to(input_dir) if _is_relative_to(Path(dst), input_dir) else Path(dst)
                    src_display = self._truncate_path(str(rel_src))
                    dst_display = self._truncate_path(str(rel_dst))
                    console.print(f"  [dim]{_rich_safe(src_display)}[/dim]")
                    console.print(f"    → [green]{_rich_safe(dst_display)}[/green] ({count} file(s))")
                    console.print()
                if len(mappings) > 10:
                    console.print(f"  [dim]... and {len(mappings) - 10} more folder(s)[/dim]")
                    console.print()
            else:
                console.print("[dim]No folders to process with this mode.[/dim]")
                console.print()

        # Show menu and handle choice
        return self._wizard_auto_mode_menu(workflow, analyzer, analysis, rec_mode, mode_names, mappings)

    def _wizard_auto_mode_menu(self, workflow: Dict, analyzer: FolderAnalyzer, analysis: Dict, 
                               rec_mode: int, mode_names: Dict, mappings: List) -> bool:
        """Show the auto mode menu and handle user choice."""
        input_dir = Path(workflow['input_dir'])
        rec_name = mode_names.get(rec_mode, f"Mode {rec_mode}")
        
        if RICH_AVAILABLE and console:
            console.print("[bold]What to do:[/bold]")
            options = [
                ("Y", f"Use Mode {rec_mode} — {rec_name}", True),
                ("P", "Generate manifest CSV (edit in Excel)", True),
                ("V", "View manifest", True),
                ("N", "Choose mode manually", True),
            ]

            for key, desc, avail in options:
                console.print(f"[{key}] {desc}")

            choice = Prompt.ask("Select", choices=["Y", "P", "V", "N"], default="Y")
        else:
            print("What to do:")
            print("[Y] Use this mode")
            print("[P] Generate manifest CSV")
            print("[V] View manifest")
            print("[N] Choose mode manually")
            choice = input("Select [Y/P/V/N]: ").strip().upper()
            if choice not in ["Y", "P", "V", "N"]:
                choice = "Y"

        if choice == "Y":
            workflow['mode'] = rec_mode
            # Mode 7: propagate the auto-detected subfolder shown in the
            # preview, otherwise the run behaves like mode 6 (all subfolders)
            # and the preview lied.
            if rec_mode == 7 and mappings:
                sub_names = {Path(src).name for src, _, _ in mappings}
                if len(sub_names) == 1:
                    workflow.setdefault('mode_config', {})['export_subfolder'] = sub_names.pop()
            workflow['auto_mode_used'] = True

            if RICH_AVAILABLE and console:
                console.print(f"[green]✓ Using Mode {rec_mode} — {rec_name}[/green]")
            else:
                print(f"✓ Using Mode {rec_mode} — {rec_name}")

            return True

        elif choice == "P":
            # Generate manifest CSV
            manifest_path = self._generate_manifest(analyzer, analysis, rec_mode)
            if manifest_path:
                if RICH_AVAILABLE and console:
                    console.print(f"[green]✓ Manifest saved to:[/green] {_rich_safe(manifest_path)}")
                    console.print("[dim]Edit in Excel, then run with [M] Run from manifest[/dim]")
                else:
                    print(f"✓ Manifest saved to: {manifest_path}")
                    print("Edit in Excel, then run with [M] Run from manifest")
            # After generating, ask again
            return self._wizard_auto_mode_post_manifest(workflow, analyzer, analysis, rec_mode, mode_names, mappings)

        elif choice == "V":
            # View manifest
            manifest_path = self._get_latest_manifest()
            if manifest_path and Path(manifest_path).exists():
                self._view_manifest(manifest_path, input_dir)
                # After viewing, ask again
                return self._wizard_auto_mode_post_manifest(workflow, analyzer, analysis, rec_mode, mode_names, mappings)
            else:
                if RICH_AVAILABLE and console:
                    console.print("[yellow]No manifest found. Generate one first with [P][/yellow]")
                else:
                    print("No manifest found. Generate one first with [P]")
                # No manifest - return to same menu so user can select [P]
                return self._wizard_auto_mode_menu(workflow, analyzer, analysis, rec_mode, mode_names, mappings)

        else:
            if RICH_AVAILABLE and console:
                console.print("[dim]Switching to manual mode selection...[/dim]")
            else:
                print("Switching to manual mode selection...")
            return self._wizard_select_mode_manual(workflow)

    def _wizard_auto_mode_post_manifest(self, workflow: Dict, analyzer: FolderAnalyzer, analysis: Dict, rec_mode: int, mode_names: Dict, mappings: List) -> bool:
        """Ask what to do after manifest was generated/viewed."""
        if RICH_AVAILABLE and console:
            console.print()
            console.print("[bold]After manifest:[/bold]")
            options = [
                ("Y", f"Use Mode {rec_mode} — {mode_names.get(rec_mode, f'Mode {rec_mode}')}", True),
                ("M", "Run from manifest", True),
                ("N", "Choose mode manually", True),
            ]
            for key, desc, avail in options:
                console.print(f"[{key}] {desc}")
            choice = Prompt.ask("Select", choices=["Y", "M", "N"], default="Y")
        else:
            print()
            print("After manifest:")
            print("[Y] Use this mode")
            print("[M] Run from manifest")
            print("[N] Choose mode manually")
            choice = input("Select [Y/M/N]: ").strip().upper()
            if choice not in ["Y", "M", "N"]:
                choice = "Y"

        if choice == "Y":
            workflow['mode'] = rec_mode
            # Mode 7: same propagation as the main auto-mode menu — without the
            # detected subfolder, mode 7 behaves like mode 6 (all subfolders).
            if rec_mode == 7 and mappings:
                sub_names = {Path(src).name for src, _, _ in mappings}
                if len(sub_names) == 1:
                    workflow.setdefault('mode_config', {})['export_subfolder'] = sub_names.pop()
            workflow['auto_mode_used'] = True
            return True
        elif choice == "M":
            manifest_path = self._get_latest_manifest()
            if manifest_path and Path(manifest_path).exists():
                return self._wizard_run_from_manifest(workflow)
            else:
                if RICH_AVAILABLE and console:
                    console.print("[red]No manifest found![/red]")
                else:
                    print("No manifest found!")
                return False
        else:
            return self._wizard_select_mode_manual(workflow)

    def _truncate_path(self, path: str, max_len: int = 50) -> str:
        """Truncate path for display, keeping start and end."""
        if len(path) <= max_len:
            return path
        # Keep start and end
        start_len = max_len // 2 - 2
        return path[:start_len] + "..." + path[-max_len//2+3:]

    def _generate_manifest(self, analyzer: FolderAnalyzer, analysis: Dict, mode: int) -> Optional[str]:
        """Generate manifest CSV and return path."""
        import csv
        from datetime import datetime

        mappings = analyzer.generate_manifest(analysis, mode)

        if not mappings:
            if RICH_AVAILABLE and console:
                console.print("[yellow]No folders to add to manifest.[/yellow]")
            else:
                print("No folders to add to manifest.")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        manifest_dir = SCRIPT_DIR / "manifests"
        manifest_dir.mkdir(exist_ok=True)
        manifest_path = manifest_dir / f"manifest_{timestamp}.csv"

        # utf-8-SIG, not plain utf-8: without the BOM, Excel opens a .csv using
        # the system ANSI codepage, so a path like "240419_山羊公園" comes out as
        # mojibake — and saving from there would write those broken bytes back.
        # The BOM costs 3 bytes and makes Excel detect UTF-8 correctly.
        with open(manifest_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            # The Direction column binds the manifest to the workflow that
            # generated it, so a TIFF->JXL manifest is never accidentally
            # replayed by a JXL->TIFF session (which would run the wrong script).
            # The five derivative columns follow for the directions that accept
            # them (empty cell = the option is NOT applied on that row): the
            # user opens the CSV in Excel and only fills in what differs.
            # When ANY generated row is mode 6/7 the export columns are appended
            # after them — ExportMarker/ExportSubfolder in every direction,
            # ExportJxlFolder only where the child accepts the flag. Their empty
            # cell keeps the run's marker/subfolder/folder, so filling one row
            # never resets the others.
            direction = f"{analyzer.origin}2{analyzer.dest}"
            with_options = direction in _MANIFEST_DERIVATIVE_DIRECTIONS
            with_exclude = direction in _MANIFEST_OPTION_DIRECTIONS["excludefolders"]
            with_export = any(entry_mode in (6, 7)
                              for _s, _d, _c, entry_mode in mappings)
            export_header = ["ExportMarker", "ExportSubfolder"]
            if direction in _MANIFEST_OPTION_DIRECTIONS["exportjxlfolder"]:
                export_header.append("ExportJxlFolder")
            header = ["Source", "Destination", "Mode", "Direction"]
            if with_options:
                header += ["OutputICC", "Resize", "Sharpen", "RenameFrom",
                           "RenameTo"]
            # ExcludeFolders is offered for the two TIFF directions whether
            # or not mode 6/7 appears: the flag bites in every recursive
            # mode. Empty cell = the row keeps the run's '-' answer (or
            # whatever 'Repeat last workflow' stored).
            if with_exclude:
                header += ["ExcludeFolders"]
            if with_export:
                header += export_header
            writer.writerow(header)
            for src, dst, count, entry_mode in mappings:
                row = [src, dst, entry_mode, direction]
                if with_options:
                    row += ["", "", "", "", ""]
                if with_exclude:
                    row += [""]
                if with_export:
                    row += [""] * len(export_header)
                writer.writerow(row)

        return str(manifest_path)

    @staticmethod
    def _open_manifest_for_read(manifest_path: str):
        """Open a manifest CSV. Returns a text stream, or None if undecodable.

        'utf-8-sig' reads both what we write (UTF-8 **with BOM**, see
        _generate_manifest) and a plain UTF-8 file, and strips the BOM — left in
        place it lands in the first cell and breaks the "is this row the header?"
        check, silently turning the header into a data row.

        If the file is not valid UTF-8 at all, Excel re-saved it in the system
        ANSI codepage. We deliberately do NOT guess an encoding here: guessing
        wrong yields a path that looks plausible and points somewhere else, and
        these paths drive a converter that can delete sources in mode 8. Refuse
        and tell the user how to fix it. (Pure-ASCII manifests are valid UTF-8,
        so this only ever triggers when there really are non-ASCII paths.)
        """
        try:
            with open(manifest_path, 'r', encoding='utf-8-sig') as f:
                return io.StringIO(f.read())
        except UnicodeDecodeError:
            return None

    def _get_latest_manifest(self) -> Optional[str]:
        """Get the most recent manifest file."""
        manifest_dir = SCRIPT_DIR / "manifests"
        if not manifest_dir.exists():
            return None
        manifests = list(manifest_dir.glob("manifest_*.csv"))
        if not manifests:
            return None
        return str(sorted(manifests, key=lambda p: p.stat().st_mtime, reverse=True)[0])

    def _pick_manifest(self) -> Optional[str]:
        """Pick a manifest from manifests/ (newest first). With several files
        the user chooses; with one it is used directly. Previously only the
        newest manifest was reachable, making older ones unusable from the UI.
        """
        manifest_dir = SCRIPT_DIR / "manifests"
        if not manifest_dir.exists():
            return None
        manifests = sorted(manifest_dir.glob("manifest_*.csv"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        if not manifests:
            return None
        if len(manifests) == 1:
            return str(manifests[0])
        shown = manifests[:10]
        if RICH_AVAILABLE and console:
            console.print("[bold]Available manifests (newest first):[/bold]")
            for i, m in enumerate(shown, 1):
                console.print(f"  [{i}] {_rich_safe(m.name)}")
            choice = Prompt.ask("Which manifest?",
                                choices=[str(i) for i in range(1, len(shown) + 1)], default="1")
            return str(shown[int(choice) - 1])
        print("Available manifests (newest first):")
        for i, m in enumerate(shown, 1):
            print(f"  [{i}] {m.name}")
        raw = input("Which manifest? [1]: ").strip() or "1"
        try:
            idx = max(1, min(int(raw), len(shown))) - 1
        except ValueError:
            idx = 0
        return str(shown[idx])

    def _view_manifest(self, manifest_path: str, input_dir: Path) -> None:
        """View manifest contents in a table."""
        import csv

        if not Path(manifest_path).exists():
            return

        entries = []
        stream = self._open_manifest_for_read(manifest_path)
        if stream is None:
            self._print_error(
                f"Manifest is not UTF-8: {manifest_path}\n"
                f"Excel re-saved it in the system ANSI codepage. Re-open it and use "
                f"'Save As' -> 'CSV UTF-8 (comma delimited)', or re-generate it.")
            return
        with stream as f:
            reader = csv.reader(f)
            first_row = next(reader, None)
            rows = []
            if first_row is not None:
                if _is_manifest_header_row(first_row):
                    rows = list(reader)
                else:
                    rows = [first_row] + list(reader)
            for row in rows:
                if row and not (len(row) == 1 and row[0].strip().startswith('#')):
                    source = row[0].strip()
                    dest = row[1].strip() if len(row) > 1 else source
                    mode = row[2].strip() if len(row) > 2 else "?"
                    if not source.startswith('#'):
                        entries.append((source, dest, mode))

        if not entries:
            if RICH_AVAILABLE and console:
                console.print("[yellow]Manifest is empty.[/yellow]")
            else:
                print("Manifest is empty.")
            return

        if RICH_AVAILABLE and console:
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("#", justify="right", style="dim")
            table.add_column("Source", style="red")
            table.add_column("Destination", style="green")
            table.add_column("Mode", style="magenta")

            for i, (src, dst, mode) in enumerate(entries, 1):
                src_display = self._truncate_path(src)
                dst_display = self._truncate_path(dst)
                table.add_row(str(i), _rich_safe(src_display), _rich_safe(dst_display), mode)

            console.print(Panel(table, title=f"[bold]Manifest[/bold] — {_rich_safe(manifest_path)}", border_style="blue"))
            console.print(f"[dim]Total: {len(entries)} entry(ies)[/dim]")
        else:
            print(f"\n=== Manifest: {manifest_path} ===")
            print(f"Total: {len(entries)} entry(ies)\n")
            for i, (src, dst, mode) in enumerate(entries, 1):
                print(f"  {i}. {src}")
                print(f"     -> {dst} (mode {mode})")
                print()

    def _load_manifest_entries(self, manifest_path: str,
                               origin_format: str, dest_format: str,
                               row_options: Optional[List] = None) -> Optional[List[Tuple]]:
        """Parse and validate a manifest CSV. Returns the entries, or None (with
        an explanation already printed) when the file cannot be trusted.

        Shared by the wizard and by "Repeat last workflow" so both get the same
        Mode/traversal/direction guards — these decide which script runs on which
        folders, and mode 8 deletes sources, so a repeat must never take a laxer
        path than the wizard did.

        Path anchoring: RELATIVE source/dest cells resolve against the CSV's own
        folder, not against the wrapper's CWD. A scheduled run (--run-preset from
        Task Scheduler) starts in C:\\Windows\\System32 — a preset whose manifest
        carried relative paths used to scan, convert and delete inside the
        scheduler's working directory. Absolute paths pass through untouched;
        '..' is refused outright either way.

        `row_options` (optional): when a list is passed, it is filled with one
        dict per entry, aligned with the returned list — the parsed values of
        the optional columns after Direction (OutputICC/Resize/Sharpen/
        RenameFrom/RenameTo and the export columns ExportMarker/
        ExportSubfolder/ExportJxlFolder, plus ExcludeFolders). A column
        PRESENT in the CSV overrides
        the wizard's answer for that option on that row (empty cell = not
        applied; an empty Export* cell keeps the run's value); absent columns
        never appear in the dicts. Any invalid value refuses the whole
        manifest.
        """
        entries = []
        directions = set()
        csv_dir = Path(manifest_path).resolve().parent
        anchored: List[str] = []

        def _anchor(p: str) -> str:
            """Anchor one manifest path against the CSV's folder (see above)."""
            if not p:
                return p
            q = Path(p)
            if q.is_absolute():
                return p
            anchored.append(p)
            return str(csv_dir / q)

        stream = self._open_manifest_for_read(manifest_path)
        if stream is None:
            self._print_error(
                f"Manifest is not UTF-8: {manifest_path}\n"
                f"Excel re-saved it in the system ANSI codepage, so the paths cannot be "
                f"decoded reliably — and a wrongly-decoded path is worse than no run at all. "
                f"Re-open it in Excel and use 'Save As' -> 'CSV UTF-8 (comma delimited)', "
                f"or re-generate the manifest.")
            return None
        with stream as f:
            import csv
            reader = csv.reader(f)
            first_row = next(reader, None)
            # Only skip the first row if it really is the header; a manifest
            # whose header line was deleted must not silently lose entry #1.
            rows = []
            opt_index: Dict[str, int] = {}
            if first_row is not None:
                if _is_manifest_header_row(first_row):
                    rows = list(reader)
                    # Optional columns live AFTER Direction (index 4+). A name
                    # outside the known set refuses the manifest: a typo like
                    # "Resise" must not be silently ignored, or the user would
                    # believe the resize happened.
                    for ci, cell in enumerate(first_row[4:], 4):
                        cname = cell.strip().lower()
                        if not cname:
                            continue
                        if cname not in _MANIFEST_OPTION_COLUMNS:
                            accepted = ", ".join(_MANIFEST_OPTION_DISPLAY[c]
                                                 for c in _MANIFEST_OPTION_COLUMNS)
                            self._print_error(
                                f"Unknown manifest column {cell.strip()!r} — the "
                                f"optional columns after Direction are: {accepted}.")
                            return None
                        opt_index[cname] = ci
                else:
                    rows = [first_row] + list(reader)
            current_direction_pre = f"{origin_format}2{dest_format}"
            for _row_n, row in enumerate(rows, 1):
                if row and len(row) >= 1:
                    source = row[0].strip()
                    is_comment = not source or source.startswith('#')
                    if is_comment:
                        # Comment/blank rows are fully INERT: no cell of theirs
                        # is validated. The Mode cell used to be parsed BEFORE
                        # this check, so a '# row,dest,9.5,dir' comment refused
                        # the whole manifest (Direction already worked this way).
                        continue
                    # Empty Destination cell must fall back to Source, not to
                    # "" — Path("") becomes "." (the CWD) downstream.
                    dest = (row[1].strip() or source) if len(row) > 1 else source
                    # Mode cell: Excel often formats integers as "7.0" — a
                    # naive isdigit() check would silently turn that into
                    # mode 0 and scatter outputs next to the sources. For the
                    # same reason a FRACTION ("7.5") is refused outright rather
                    # than truncated to 7.
                    # None = "no Mode cell" (legacy manifest), which enables
                    # marker-based detection downstream; an explicit 0 is kept.
                    entry_mode = None
                    if len(row) > 2 and row[2].strip():
                        try:
                            _mode_f = float(row[2].strip())
                            if not _mode_f.is_integer():
                                raise ValueError
                            entry_mode = int(_mode_f)
                        except ValueError:
                            self._print_error(f"Invalid Mode value in manifest: {row[2].strip()!r} (row: {source})")
                            return None
                        if not 0 <= entry_mode <= 8:
                            self._print_error(f"Mode out of range (0-8) in manifest: {entry_mode} (row: {source})")
                            return None
                    if len(row) > 3 and row[3].strip():
                        directions.add(row[3].strip())
                    # Validate paths to prevent directory traversal. Check
                    # path PARTS (not a substring match, which false-
                    # positives on legitimate names like "2024..final").
                    # The check runs on the TYPED cell, before anchoring —
                    # '..' is refused whatever it would resolve against.
                    if '..' in Path(source).parts or '..' in Path(dest).parts:
                        # Refuse the whole manifest, do not skip the row.
                        # Skipping shrank the run silently: the summary
                        # counts only the entries that LOADED, so a
                        # dropped folder looks exactly like a folder that
                        # synced cleanly. Same fail-closed rule as the
                        # Mode checks above.
                        self._print_error(
                            f"Path traversal in manifest entry: {source} -> {dest}\n"
                            f"Relative paths ARE allowed — they resolve against the manifest's "
                            f"own folder, not the wrapper's working directory — but '..' is "
                            f"refused outright. Use absolute paths (or paths relative to the "
                            f"CSV folder, without '..') and run it again.")
                        return None
                    # Anchor AFTER the '..' refusal: relative paths become
                    # absolute against the CSV's folder so the guards, the
                    # cmd builder and the children all see the same
                    # location regardless of the wrapper's CWD.
                    entries.append((_anchor(source), _anchor(dest), entry_mode))
                    if row_options is not None:
                        cells = {name: (row[i] if i < len(row) else "")
                                 for name, i in opt_index.items()}
                        opts, reason = _parse_manifest_row_options(
                            cells, current_direction_pre, _anchor)
                        if reason:
                            self._print_error(
                                f"Manifest row {_row_n} ({source}): {reason}")
                            return None
                        # ExportSubfolder reaches the children as
                        # --export-subfolder, which only mode 7 honors (mode
                        # 6 processes every subfolder by design). A filled
                        # cell on any other explicit Mode is a user mistake,
                        # not a harmless no-op — refuse it. A legacy row
                        # without a Mode cell stays accepted: its mode is
                        # only resolved downstream.
                        if (opts.get("export_subfolder")
                                and entry_mode is not None
                                and entry_mode != 7):
                            self._print_error(
                                f"Manifest row {_row_n} ({source}): column "
                                f"ExportSubfolder only applies to Mode 7 rows "
                                f"(this row is mode {entry_mode}).")
                            return None
                        # Same fail-closed rule for ExportMarker and
                        # ExportJxlFolder: they reach the children as
                        # --export-marker/--export-jxl-folder, which only the
                        # modes-6/7 finders consult — a filled cell on any
                        # other explicit Mode is a dead option the
                        # confirmation panel would still show (a "scoped"
                        # delete run that actually converts the whole tree).
                        if entry_mode is not None and entry_mode not in (6, 7):
                            for _col, _key in (("ExportMarker", "export_marker"),
                                               ("ExportJxlFolder", "export_jxl_folder")):
                                if opts.get(_key):
                                    self._print_error(
                                        f"Manifest row {_row_n} ({source}): column "
                                        f"{_col} only applies to Mode 6/7 rows "
                                        f"(this row is mode {entry_mode}).")
                                    return None
                        row_options.append(opts)

        # Say the anchoring out loud ONCE per load: a relative path that used to
        # mean "relative to wherever the wrapper happened to start" now means
        # "relative to this CSV", and a scheduled run must show that in its log.
        if anchored:
            note = (f"{len(anchored)} relative path(s) in the manifest resolved "
                    f"against the manifest's own folder: {csv_dir}")
            if RICH_AVAILABLE and console:
                console.print(f"[dim]{_rich_safe(note)}[/dim]")
            else:
                print(note)

        if not entries:
            if RICH_AVAILABLE and console:
                console.print("[yellow]Manifest is empty or only has comments.[/yellow]")
            else:
                print("Manifest is empty or only has comments.")
            return None

        # Direction guard: a manifest is bound to the workflow that generated
        # it (e.g. tiff2jxl). Running it from a different session would invoke
        # the wrong script on the wrong files.
        # NOT `dest`: that name belongs to the row loop above (a Destination
        # path), and reusing it here silently produced "tiff2G:\2025".
        current_direction = f"{origin_format}2{dest_format}"
        if directions:
            if directions != {current_direction}:
                self._print_error(
                    f"Manifest direction {sorted(directions)} does not match the current "
                    f"workflow ({current_direction}). Generate/select a matching manifest."
                )
                return None
        else:
            # Legacy manifest without a Direction column: allow, but warn.
            warn = (f"Manifest has no Direction column (pre-v1.8.1 format). "
                    f"Assuming it matches the current workflow ({current_direction}).")
            if RICH_AVAILABLE and console:
                console.print(f"[yellow]{_rich_safe(warn)}[/yellow]")
            else:
                print(f"WARNING: {warn}")

        if opt_index:
            # Two families with OPPOSITE empty-cell semantics (docs/
            # README_manifest.md): an empty derivative cell clears the option
            # on that row, an empty Export*/ExcludeFolders cell keeps the
            # run's value. One blanket "empty = not applied" told the user the
            # opposite of what the keep-the-run columns do.
            _keep_cols = ("exportmarker", "exportsubfolder", "exportjxlfolder",
                          "excludefolders")
            names = ", ".join(_MANIFEST_OPTION_DISPLAY[c] for c in opt_index)
            cleared = [_MANIFEST_OPTION_DISPLAY[c] for c in opt_index
                       if c not in _keep_cols]
            kept = [_MANIFEST_OPTION_DISPLAY[c] for c in opt_index
                    if c in _keep_cols]
            note = (f"Manifest option columns: {names} (a filled cell overrides "
                    f"the run's answer on that row")
            if cleared:
                note += f"; an empty {'/'.join(cleared)} cell means 'not applied'"
            if kept:
                note += f"; an empty {'/'.join(kept)} cell keeps the run's value"
            if "excludefolders" in opt_index:
                note += "; ExcludeFolders '-' means no exclusion on that row"
            note += ")"
            if RICH_AVAILABLE and console:
                console.print(f"[dim]{_rich_safe(note)}[/dim]")
            else:
                print(note)

        # Invariant: one options dict per entry. Catches a desync between the
        # entries list and row_options on the spot instead of misaligning
        # recipes with rows at execution time.
        assert row_options is None or len(row_options) == len(entries), (
            f"manifest row options out of sync: {len(entries)} entries, "
            f"{len(row_options)} option rows")

        return entries

    def _confirm_manifest_entries(self, manifest_path: str, entries: List[Tuple],
                                  row_options: Optional[List] = None) -> bool:
        """Preview the entries and ask for a go-ahead. Used by the wizard; the
        attended repeat ("Repeat last workflow") re-reads the CSV with the same
        loader guards but shows only the manifest name and entry count.

        When the manifest carries option columns, each row shows a short recipe
        summary (sRGB · long:2048 · screen · ProPhoto→sRGB) so the user sees
        what differs from the wizard's answers BEFORE confirming."""
        _summaries = ([_row_options_summary(o) for o in row_options]
                      if row_options else None)
        _show_opts = bool(_summaries) and any(_summaries)
        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Manifest:[/bold cyan] {_rich_safe(manifest_path)}")
            console.print(f"[bold]Entries to process:[/bold] {len(entries)}")
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("#", justify="right", style="dim")
            table.add_column("Source", style="red")
            table.add_column("Destination", style="green")
            table.add_column("Mode", style="magenta")
            if _show_opts:
                table.add_column("Options", style="cyan")
            for i, (src, dst, mode) in enumerate(entries[:15], 1):
                row = [str(i), _rich_safe(self._truncate_path(src)), _rich_safe(self._truncate_path(dst)),
                       str(mode)]
                if _show_opts:
                    row.append(_summaries[i - 1])
                table.add_row(*row)
            console.print(table)
            if len(entries) > 15:
                console.print(f"[dim]... and {len(entries) - 15} more entries[/dim]")
            console.print()

            proceed = Confirm.ask("Proceed with manifest?", default=True)
        else:
            print(f"\nManifest: {manifest_path}")
            print(f"Entries to process: {len(entries)}\n")
            for i, (src, dst, mode) in enumerate(entries[:15], 1):
                print(f"  {i}. {src}")
                _opt = (f"   [{_summaries[i - 1]}]"
                        if _show_opts and _summaries[i - 1] else "")
                print(f"     -> {dst} (mode {mode}){_opt}")
            if len(entries) > 15:
                print(f"  ... and {len(entries) - 15} more entries")
            print()
            proceed_input = input("Proceed with manifest? [Y/n]: ").strip().lower()
            proceed = not proceed_input.startswith('n')

        return proceed

    def _wizard_run_from_manifest(self, workflow: Dict) -> bool:
        """Step 4 alternative: run the workflow from a manifest CSV."""
        manifest_path = self._pick_manifest()
        if not manifest_path or not Path(manifest_path).exists():
            if RICH_AVAILABLE and console:
                console.print("[red]No manifest found![/red]")
            else:
                print("No manifest found!")
            return False

        row_options: List = []
        entries = self._load_manifest_entries(
            manifest_path, workflow['origin_format'], workflow['dest_format'],
            row_options=row_options)
        if entries is None:
            return False

        if not self._confirm_manifest_entries(manifest_path, entries,
                                              row_options=row_options):
            return False

        # A manifest with mode-8 rows promises "DELETE originals", but the
        # children only delete with --delete-source — and nothing on this path
        # ever asked. The HHMM token itself is charged at execution time, after
        # the user had the chance to dry-run.
        #
        # The QUESTION was keyed on mode 8 and the ANSWER was not: there is one
        # delete_source for the whole workflow and the cmd builder appends
        # --delete-source to EVERY entry, which the children now honour in every
        # mode. So a manifest of mode-8 rows plus mode-3 rows asked about the
        # mode-8 ones and then deleted the mode-3 sources too. Ask about what
        # actually happens, and name the rows it will happen to.
        _delete_modes = sorted({m for _, _, m in entries if m is not None})
        if any(m == 8 for _, _, m in entries):
            _n = len(entries)
            _other = [m for m in _delete_modes if m != 8]
            _scope = (f"ALL {_n} entries" if _other else f"all {_n} mode-8 entries")
            _extra = (f" — including the mode {'/'.join(str(m) for m in _other)} "
                      f"row(s), whose originals are deleted just the same"
                      if _other else "")
            _q = (f"Manifest contains DELETE-mode (8) entries. Deleting is a run-wide "
                  f"setting, so answering yes deletes the originals of {_scope}{_extra}. "
                  f"Delete originals after conversion? (IRREVERSIBLE)")
            if RICH_AVAILABLE and console:
                console.print()
                console.print(Panel(f"[bold red]{_q}[/bold red]", border_style="red"))
                delete = Confirm.ask("[bold red]Delete originals?[/bold red]", default=False)
            else:
                print(f"\n{_q}")
                delete = input("Delete originals? [y/N]: ").strip().lower().startswith('y')
            if delete:
                workflow['delete_source'] = True
                # The same gates the [D] gateway charges for a single run. A
                # manifest converts more files than anything else here, and it
                # used to reach the unlink with none of them offered.
                # "Collapses" is per ENTRY, so the question is worth asking as
                # soon as ONE entry drops folder structure.
                # A legacy manifest carries no Mode cell (None): the mode is
                # detected per folder later and can come back 6 or 7, both of
                # which collapse. Unknown counts as collapsing — the cost of
                # asking one extra question is nothing next to skipping it.
                _collapsing = sorted({m for s, d, m in entries
                                      if m is None or _run_collapses_structure(m, d, s)},
                                     key=lambda m: (m is None, m))
                _shown = "/".join("?" if m is None else str(m) for m in _collapsing)
                self._ask_delete_options(
                    workflow,
                    collapses=bool(_collapsing),
                    scope_label=(f"Mode {_shown} entries in this manifest "
                                 f"drop folder structure"))
            else:
                console_msg = ("Every entry will run WITHOUT deleting originals "
                               "(source and output will coexist).")
                if RICH_AVAILABLE and console:
                    console.print(f"[yellow]{console_msg}[/yellow]")
                else:
                    print(console_msg)

        # For manifest mode, we set mode to special value 99 (manifest mode)
        workflow['mode'] = 99  # Special mode for manifest execution
        workflow['manifest_entries'] = entries
        workflow['manifest_row_options'] = row_options
        workflow['manifest_path'] = manifest_path
        # auto_mode_used stays False: a manifest run must not report
        # "Auto Mode: Yes" in the Step-7 summary.

        return True

    def _wizard_select_mode(self, workflow: Dict) -> bool:
        """Step 4: Organization Modes — now with [A] Auto Mode"""
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        export_marker = self.config.config.export_marker

        m1_name, m3_name = _dest_folder_names(origin, dest)
        if origin == 'jxl' and dest == 'jxl':
            # Same extension in and out: the recompressor has NO "side by side"
            # — mode 0/8 outputs REPLACE the source file (in-place promotion by
            # os.replace). The [?] details already say so (see
            # _show_mode_details_and_select, and its comment about how saying
            # "stay side by side" hid the most destructive default the wizard
            # offers) — but the menu itself, where the decision is actually
            # made, still sold modes 0/8 as "originals kept". Mirror the honest
            # wording here, at the moment of choice.
            m0_desc = ("Replaces each source JXL with the recompressed one, in the "
                       "same folder (originals [bold red]NOT kept[/bold red])")
            m8_desc = ("Like mode 0, RECURSIVE: every source JXL is REPLACED in "
                       "place, all subfolders (originals [bold red]NOT kept[/bold red])")
        else:
            m0_desc = f"{origin.upper()} and {dest.upper()} side by side in same folder"
            m8_desc = ("JXL next to each source file, all subfolders "
                       "(originals kept — use [D] to delete)")
        modes = [
            ("0", "In-place",
             m0_desc),
            ("1", "Subfolder",
             f"Creates [green]'{m1_name}'[/green] subfolder"),
            ("2", "Flat -> output folder",
             f"All files from subfolders merged to single output folder (recursive)"),
            ("3", "Recursive subfolders",
             f"Creates [green]'{m3_name}'[/green] in each subfolder"),
            ("4", "Folder rename (suffix swap)",
             f"Replaces {origin.upper()} with {dest.upper()} in folder name"),
            ("5", "Sibling folder",
             f"Creates a sibling folder (e.g. [green]JXL_16bits[/green]) next to each source folder"),
            ("6", f"Marker [green]{export_marker}[/green] (full)",
             f"ONLY files INSIDE folders matching '{export_marker}' — ignores everything outside"),
            ("7", f"Marker [green]{export_marker}[/green] (specific subfolder)",
             f"Like mode 6, but only one subfolder of the export marker (asked in Step 5; empty = all = mode 6)"),
            ("8", "In-place recursive",
             m8_desc)
        ]

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Step 4: Organization Mode[/bold cyan]")
            console.print("[dim]The export marker (e.g. '_EXPORT') is configurable in Settings (menu 9)[/dim]")
            console.print("[dim]Other folder names (e.g. 'converted_jxl', 'JXL_16bits', '16B_TIFF') are the scripts' settings: Script settings (menu S)[/dim]")

            # Highlight auto mode and manifest
            console.print()
            console.print(Panel.fit(
                "[bold yellow][A] Auto Mode[/bold yellow] — analyze folder structure and recommend best mode",
                border_style="yellow"
            ))
            console.print()

            for key, name, desc in modes:
                # [D] below is the DELETING entry and carries the red, or the
                # warning colour would point at the wrong option. But deletion
                # is not the only way this menu can lose a file: for a JXL->JXL
                # run, modes 0 and 8 OVERWRITE the source in place (their
                # descriptions carry the red "NOT kept" warning) — the entries
                # themselves stay green so [D] remains the one red option.
                console.print(f"[{key}] [bold green]{name}[/bold green]")
                console.print(f"    {desc}\n")

            console.print("[M] [bold]Run from manifest[/bold] — execute a previously generated manifest CSV")
            console.print(self._delete_entry_line())
            console.print("[?] [bold yellow]See detailed mode explanation[/bold yellow]")
            console.print()

            valid_choices = [m[0] for m in modes] + ["?", "A", "M", "D", "a", "m", "d"]
            while True:
                choice = Prompt.ask("Select mode", choices=valid_choices)
                if choice:
                    choice = choice.upper()
                    break
        else:
            print(f"\n--- Step 4: Organization Mode ---")
            print("[A] Auto Mode — analyze folder and recommend best mode")
            print()
            for key, name, desc in modes:
                print(f"[{key}] {name}")
                clean = (desc.replace("[bold green]", "").replace("[/bold green]", "")
                         .replace("[bold red]", "").replace("[/bold red]", "")
                         .replace("[bold blue]", "").replace("[/bold blue]", "")
                         .replace("[bold cyan]", "").replace("[/bold cyan]", "")
                         .replace("[bold yellow]", "").replace("[/bold yellow]", "")
                         .replace("[bold]", "").replace("[/bold]", "")
                         .replace("[green]", "").replace("[/green]", "")
                         .replace("[cyan]", "").replace("[/cyan]", "")
                         .replace("[red]", "").replace("[/red]", "")
                         .replace("[yellow]", "").replace("[/yellow]", "")
                         .replace("[dim]", "").replace("[/dim]", ""))
                print(f"    {clean}\n")
            print("[M] Run from manifest — execute a previously generated manifest CSV")
            print(self._delete_entry_line(plain=True))
            print("[?] See detailed mode explanation")
            print()

            valid_choices = [m[0] for m in modes] + ["?", "A", "M", "D"]
            while True:
                choice = input("Mode (0-8, D to delete originals, A for auto, M for manifest, ? for details): ").strip().upper()
                if choice in valid_choices:
                    break

        # Handle "?" - show detailed explanations
        if choice == "?":
            return self._show_mode_details_and_select(workflow)

        # Handle Auto Mode
        if choice == "A":
            return self._wizard_auto_mode(workflow)

        # Handle Manifest
        if choice == "M":
            return self._wizard_run_from_manifest(workflow)

        _handled = self._handle_mode_choice(workflow, choice, modes)
        if _handled is not None:
            return _handled

        workflow['mode'] = int(choice)

        note_lines = [
            "[bold yellow]Configurable items:[/bold yellow] the export marker (e.g. '_EXPORT') can be customized",
            "in [bold cyan]Settings (menu 9)[/bold cyan]. Folder names like 'converted_jxl'",
            "are the scripts' own settings: [bold cyan]Script settings (menu S)[/bold cyan].",
        ]
        if RICH_AVAILABLE and console:
            console.print()
            console.print(Panel(
                "\n".join(note_lines),
                title="[yellow]Tip[/yellow]",
                border_style="yellow"
            ))
        else:
            print()
            for line in note_lines:
                print(line.replace("[bold green]", "").replace("[/bold green]", "")
                      .replace("[bold red]", "").replace("[/bold red]", "")
                      .replace("[bold blue]", "").replace("[/bold blue]", "")
                      .replace("[bold cyan]", "").replace("[/bold cyan]", "")
                      .replace("[bold yellow]", "").replace("[/bold yellow]", "")
                      .replace("[bold]", "").replace("[/bold]", "")
                      .replace("[green]", "").replace("[/green]", "")
                      .replace("[cyan]", "").replace("[/cyan]", "")
                      .replace("[red]", "").replace("[/red]", "")
                      .replace("[yellow]", "").replace("[/yellow]", "")
                      .replace("[dim]", "").replace("[/dim]", ""))

        return True

    # Every mode selector has to offer [D], or deletion becomes unreachable from
    # whichever one forgets it — which is exactly what happened when [D] was
    # added to the Step-4 menu alone: Auto Mode -> "pick manually" and the "?"
    # detail view both lost the delete workflow entirely, silently.
    #
    # The three menus deliberately keep their own presentation (full, compact,
    # detailed), so what is shared is the CHOICE, not the rendering:
    # _DELETE_CHOICE goes into every valid_choices list and _handle_mode_choice
    # resolves it. tests/test_delete_any_mode.py enumerates the selectors and
    # asserts both, so a fourth one cannot quietly skip it.
    _DELETE_CHOICE = "D"

    def _delete_entry_line(self, plain: bool = False) -> str:
        """The [D] line, worded the same wherever the menu is drawn."""
        if plain:
            return ("[D] Convert and DELETE the originals  ** IRREVERSIBLE **\n"
                    "    Pick any layout above; sources are removed once each output is\n"
                    "    written to that layout's destination and verified")
        return ("[D] [bold red]Convert and DELETE the originals[/bold red] ⚠️  — pick any "
                "layout above, then the sources are removed once each output is written "
                "and verified")

    def _handle_mode_choice(self, workflow: Dict, choice: str, modes: List):
        """Resolve a mode-menu key. Returns None when it was a plain layout."""
        if choice.upper() == self._DELETE_CHOICE:
            return self._wizard_delete_gateway(workflow, modes)
        return None

    # Conversion types where an already-archived source can be tied to its
    # output: the transcoder stores the source's md5 keyed by the output's name.
    _PROVABLE_CONVERSIONS = ('transcode_lossless', 'jxl_to_jpeg_lossless')
    # ...and the ones where nothing can: no checksum is stored and the output
    # cannot reproduce the source.
    _LOSSY_CONVERSIONS = ('convert_lossy', 'jxl_to_jpeg_force', 'jxl_to_png',
                          'jxl_to_jpeg_auto')

    def _confirm_lossy_delete_skipped(self, workflow: Dict) -> None:
        """Extra gate for `delete_skipped` on a direction that can prove nothing.

        Lives here rather than inside the [D] wizard so that REPEATS and PRESETS
        get it too — they rebuild advanced_options from the stored session and
        never walk through [D]. The wizard marks the workflow once it has asked,
        so the question is never posed twice in one run.

        Never cancels the run: declining turns `delete_skipped` off in
        `advanced_options` — the dict the cmd builder reads — and the run
        continues without it. (It was annotated `-> bool` and documented as
        returning False on cancel, which no path ever did, so the
        `if not ...: return False` at both call sites was dead code stating a
        contract this function does not have.)

        An unreadable stdin counts as declining, like every other gate here: a
        closed pipe raised EOFError out of `input()` and took the run down with
        a traceback instead of quietly keeping the originals.
        """
        adv = workflow.get('advanced_options') or {}
        if not (adv.get('delete_source') and adv.get('delete_skipped')):
            return
        if workflow.get('conversion_type', '') not in self._LOSSY_CONVERSIONS:
            return
        if workflow.get('_lossy_skip_confirmed') or workflow.get('dry_run'):
            return

        # W2 (2026-10-08 audit): the transcoder's lossy gate requires the
        # output's provenance marker now (T1); "nothing ties it to the
        # original" was no longer true. What remains true is that the marker
        # names the source FILE (and the hash its bytes had then: an original
        # replaced since is kept) and nothing compares the pixels.
        msg = ("This direction is LOSSY and stores no checksum. For originals that were "
               "already converted, what ties the existing output to the original being "
               "deleted is its provenance marker (it names the source FILE and the hash "
               "of its bytes) and a structural check — nothing compares the pixels.")
        if RICH_AVAILABLE and console:
            console.print()
            console.print(Panel(f"[bold red]{_rich_safe(msg)}[/bold red]", border_style="red"))
            go = Confirm.ask("[bold red]Delete already-converted originals anyway?[/bold red]",
                             default=False)
        else:
            print(f"\n{msg}")
            try:
                go = input("Delete already-converted originals anyway? [y/N]: "
                           ).strip().lower().startswith('y')
            except (EOFError, KeyboardInterrupt):
                print()
                go = False
        if not go:
            adv['delete_skipped'] = False
            note = "Already-converted originals will be KEPT."
            if RICH_AVAILABLE and console:
                console.print(f"[yellow]{_rich_safe(note)}[/yellow]")
            else:
                print(note)
        workflow['_lossy_skip_confirmed'] = True

    def _count_origin_files(self, workflow: Dict, mode: int) -> int:
        """How many source files this mode would ACTUALLY see, for the delete
        preview. Returns -1 when it cannot be determined.

        Asks the child's OWN finder wherever possible. Counting by extension
        was wrong for exactly the modes this user runs most: mode 6 announced
        "23 TIFF file(s)" for a run that touches 3, because the marker filter
        (and the tool-output filters) were ignored. The count is the whole point
        of the confirmation — it is what makes a wrong folder visible before the
        HHMM token is charged — so an inflated one is worse than none.
        """
        origin = workflow.get('origin_format')
        dest = workflow.get('dest_format')
        try:
            root = Path(workflow['input_dir'])
            exts = FolderAnalyzer(root, origin, dest)._get_extensions(origin)
            if root.is_file():
                return 1 if root.suffix.lower() in exts else 0

            child = None
            try:
                if origin == 'tiff' and dest == 'jxl':
                    import jxl_tiff_encoder as child
                    finders = {6: 'find_tiffs_mode6', 7: 'find_tiffs_mode7',
                               0: 'find_files_mode0', 1: 'find_files_mode0'}
                    recursive = 'find_tiffs_recursive'
                elif origin == 'jxl' and dest == 'tiff':
                    import jxl_tiff_decoder as child
                    finders = {6: 'find_jxls_mode6', 7: 'find_jxls_mode7',
                               0: 'find_jxls_flat', 1: 'find_jxls_flat'}
                    recursive = 'find_jxls_recursive'
                elif origin == 'jxl' and dest == 'jxl':
                    import jxl_recompressor as child
                    finders = {6: 'find_jxls_mode6', 7: 'find_jxls_mode7',
                               0: 'find_files_mode0', 1: 'find_files_mode0'}
                    recursive = 'find_jxls_recursive'
            except ImportError:
                child = None

            _mc = workflow.get('mode_config', {}) or {}
            marker = _mc.get('export_marker') or self.config.config.export_marker
            # The subfolder matters as much as the marker: mode 7 filters on it,
            # and leaving it at the script default made this count report every
            # subfolder under the marker for a run that converts exactly one of
            # them — not merely a bigger number, a DIFFERENT set of files, under
            # the panel whose visible count is the thing that catches a wrong
            # folder. The cmd builder passes it as --export-subfolder; mirror it.
            subfolder = _mc.get('export_subfolder')
            # The modes 6/7 output folder moves the destination (and, for the
            # recompressor, the folder its recursive scans must skip): the
            # child's own finders read it as a module global.
            jxl_folder = _mc.get('export_jxl_folder')

            if child is not None:
                _was_disabled = child.logger.disabled
                child.logger.disabled = True        # its finders log; this is a preview
                try:
                    # The exclusion too — the child's finders filter through
                    # their own EXCLUDE_FOLDERS global, which _with_child_marker
                    # now mirrors. Without it the delete panel would count a
                    # set the run would skip (bug #297's class again).
                    with _with_child_marker(child, marker, subfolder, jxl_folder,
                                            exclude_folders=workflow.get('exclude_folders')):
                        fn = getattr(child, finders.get(mode, recursive))
                        return len(fn(root))
                finally:
                    child.logger.disabled = _was_disabled

            # Transcoder directions: no single finder covers them (JPEG, PNG and
            # JXL are scanned separately), so count by extension with the same
            # flat/recursive split the children use.
            it = root.rglob('*') if mode in _RECURSIVE_MANIFEST_MODES else root.glob('*')
            files = [f for f in it if f.suffix.lower() in exts and f.is_file()]

            # ...and then through the child's OWN output resolver, because the
            # extension count alone was wrong in exactly the modes this user
            # runs most. The transcoder has no mode-6/7 finder: it scans
            # everything and drops files outside the export marker by returning
            # None from the resolver. Counting them anyway announced "23 file(s)"
            # for a run that touches 3 — the same over-count fixed for the
            # encoder and decoder in #269, still live here. The resolver returns
            # a path for every other mode, so this filter costs nothing there.
            try:
                import jxl_jpeg_transcoder as _tr
            except ImportError:
                return len(files)
            _was_disabled = _tr.logger.disabled
            _tr.logger.disabled = True      # the resolver warns; this is a preview
            try:
                # Same marker AND subfolder the encoder/decoder branch uses: the
                # transcoder filters modes 6/7 inside its resolver rather than in
                # a finder, but it reads the same globals.
                with _with_child_marker(_tr, marker, subfolder, jxl_folder,
                                        exclude_folders=workflow.get('exclude_folders')):
                    _decode = (origin == 'jxl')
                    return sum(1 for f in files
                               if _tr.resolve_output_transcode(f, mode, root, _decode)
                               is not None)
            finally:
                _tr.logger.disabled = _was_disabled
        except (OSError, KeyError, AttributeError):
            return -1   # unreadable: the preview says so instead of claiming 0

    def _wizard_delete_gateway(self, workflow: Dict, modes: List) -> bool:
        """[D] — convert, verify, then DELETE the originals.

        Deletion is not a layout, so it is not a mode: this asks for the layout
        (0-8) and sets `delete_source` alongside it, producing exactly the same
        `--mode N --delete-source` a CLI user would type. Reusing "8" as the
        gateway would have made `--mode 8` mean two different things.

        Three gates, each MORE specific than the last, rather than the same
        question twice — a repeated y/N trains people to answer it twice:

          1. here, a plain y/N (default No), to undo a mis-keyed 'D';
          2. after the layout is known, the CONCRETE consequence — how many
             files, from which folder, to where. This is the one that catches
             a real mistake, because a wrong folder is visible in the count;
          3. the HHMM token at execution time (execute_workflow), which cannot
             be answered by reflex and is charged only on a non-dry run.

        The token is deliberately NOT asked twice: the second prompt can roll
        into the next minute and fail spuriously (see _run_saved_session).
        """
        origin = workflow['origin_format']
        dest = workflow['dest_format']

        # --- Gate 1: undo a mis-key ---------------------------------------
        warn = (f"[D] deletes your original {origin.upper()} files after converting them. "
                f"This cannot be undone.")
        if RICH_AVAILABLE and console:
            console.print()
            console.print(Panel(f"[bold red]{_rich_safe(warn)}[/bold red]", border_style="red"))
            go = Confirm.ask("[bold]Continue?[/bold]", default=False)
        else:
            print(f"\n{warn}")
            go = input("Continue? [y/N]: ").strip().lower().startswith('y')
        if not go:
            if RICH_AVAILABLE and console:
                console.print("[dim]Cancelled — back to the mode list.[/dim]")
            else:
                print("Cancelled — back to the mode list.")
            return self._wizard_select_mode(workflow)

        # --- The layout, same 0-8 as the normal path ----------------------
        if RICH_AVAILABLE and console:
            console.print("\n[bold cyan]Where should the converted files go?[/bold cyan]")
            for key, name, _desc in modes:
                console.print(f"  [{key}] {name}")
            layout = Prompt.ask("Layout", choices=[m[0] for m in modes], default="0")
        else:
            print("\nWhere should the converted files go?")
            for key, name, _desc in modes:
                print(f"  [{key}] {name}")
            layout = ""
            while layout not in [m[0] for m in modes]:
                layout = input("Layout [0]: ").strip() or "0"
        mode = int(layout)

        # --- Gate 2: the concrete consequence -----------------------------
        m1_name, m3_name = _dest_folder_names(origin, dest)
        _jf = (workflow.get('mode_config') or {}).get('export_jxl_folder')
        where = {
            0: "the same folder as each source file",
            1: f"a '{m1_name}' subfolder in each source folder",
            2: "one flat folder, chosen in the next step",
            3: f"a '{m3_name}' subfolder in each source folder",
            4: "a renamed sibling folder (suffix swap)",
            5: "a sibling folder next to each source folder",
            6: f"<{self.config.config.export_marker}>/{_export_folder_name(origin, dest, _jf)}/",
            7: (f"<{self.config.config.export_marker}>/{_export_folder_name(origin, dest, _jf)}/"
                f" (one subfolder only)"),
            8: "the same folder as each source file (recursive)",
        }.get(mode, "the mode's destination")
        n = self._count_origin_files(workflow, mode)
        # Kept for Step 7 (W5 of the 2026-10-08 audit): this count is taken
        # BEFORE Step 5 sets the marker / mode-7 subfolder / output folder,
        # so the summary recounts and flags a difference.
        self._delete_panel_count = n
        count_str = (f"{n} {origin.upper()} file(s)" if n >= 0
                     else f"an unknown number of {origin.upper()} file(s) (folder unreadable)")
        lines = [
            f"{count_str} under:",
            f"    {workflow['input_dir']}",
            f"will be converted to {dest.upper()} in {where},",
            "and then DELETED from where they are now.",
        ]
        if RICH_AVAILABLE and console:
            console.print()
            console.print(Panel("\n".join(lines), title="[bold red]About to delete originals[/bold red]",
                                border_style="red"))
            sure = Confirm.ask("[bold red]Is that what you want?[/bold red]", default=False)
        else:
            print()
            for ln in lines:
                print(ln)
            sure = input("Is that what you want? [y/N]: ").strip().lower().startswith('y')
        if not sure:
            if RICH_AVAILABLE and console:
                console.print("[dim]Cancelled — back to the mode list.[/dim]")
            else:
                print("Cancelled — back to the mode list.")
            return self._wizard_select_mode(workflow)

        workflow['mode'] = mode
        workflow['delete_source'] = True

        # The gates in front of the unlink. Shared with the manifest path (see
        # _ask_delete_options): a manifest is the LARGEST run this wrapper
        # starts, and it used to reach the deletion without ever being offered
        # round-trip verification or a provenance choice.
        #
        # The wizard only offers a Destination for mode 2 today; asking
        # _run_collapses_structure rather than the mode set keeps this correct
        # if that ever changes, the way the manifest path already honours it
        # for mode 0 (see _manifest_needs_collision_scan).
        self._ask_delete_options(
            workflow,
            collapses=_run_collapses_structure(
                mode, (workflow.get('mode_config') or {}).get('output_dir'),
                workflow.get('input_dir')),
            scope_label=f"Mode {mode} drops folder structure")

        msg = (f"Mode {mode} + DELETE originals. You will be asked for the current "
               f"time (HHMM) once more before anything runs — a dry run never asks.")
        if RICH_AVAILABLE and console:
            console.print(f"[yellow]{_rich_safe(msg)}[/yellow]")
        else:
            print(msg)
        return True

    def _ask_delete_options(self, workflow: Dict, collapses: bool,
                            scope_label: str) -> None:
        """The three questions that stand between a conversion and the unlink.

        Extracted from the [D] gateway so the MANIFEST path gets them too. A
        manifest run converts more files than anything else the wrapper starts,
        and it reached --delete-source with none of these offered: no
        round-trip verification, no choice of how an existing output is matched
        to its source. The Step-7 summary disclosed "Verify round-trip: off",
        which is honest but not the same as being able to turn it on.

        `collapses` decides whether the provenance question is worth asking —
        in the modes that keep folder structure there is nothing to confuse.
        `scope_label` opens that question, because "Mode 5 drops folder
        structure" is the wrong sentence for a manifest whose entries each
        carry their own mode.
        """
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        # Answers are STAGED here and committed by main() only when the wizard
        # confirms: writing them straight into the config left a cancelled
        # wizard's choices in memory, persisted by the next unrelated
        # save_config() — the same invariant mode_config already keeps.
        _staged: Dict = {}

        # Round-trip verification. Offered only here, because it is a gate in
        # front of the deletion and nowhere else, and only for TIFF -> JXL and
        # JXL -> JXL — the directions where the source is the master.
        _enc_verifies = (origin == 'tiff' and dest == 'jxl'
                         and _child_bool_setting('jxl_tiff_encoder', 'VERIFY_ROUNDTRIP', True))
        if _enc_verifies:
            # On in the encoder's own settings: it verifies whatever is answered
            # here (there is no flag to turn it off), so say so instead of
            # asking a question whose "no" would do nothing.
            _msg = ("Each JXL is decoded and compared with its TIFF before the TIFF is "
                    "deleted (VERIFY_ROUNDTRIP in jxl_tiff_encoder.py).")
            if RICH_AVAILABLE and console:
                console.print(f"\n[dim]{_msg}[/dim]")
            else:
                print(f"\n{_msg}")
            verify = True
            workflow['verify_roundtrip'] = True
        elif (origin == 'tiff' and dest == 'jxl') or workflow.get('conversion_type') == 'jxl_recompress':
            vr_default = bool(self.config.config.last_verify_roundtrip)
            explain = ("Decode each JXL and compare it with the source before deleting. "
                       "Lossless: the pixels must match exactly. Lossy: a brightness and "
                       "PSNR sanity check that catches a black or scrambled encode (not "
                       "quality loss — that is what you asked for). Roughly doubles the run.")
            if RICH_AVAILABLE and console:
                console.print(f"\n[dim]{explain}[/dim]")
                verify = Confirm.ask("[cyan]Verify each output before deleting its source?[/cyan]",
                                     default=vr_default)
            else:
                print(f"\n{explain}")
                _vi = input(f"Verify each output before deleting its source? "
                            f"[{'Y/n' if vr_default else 'y/N'}]: ").strip().lower()
                verify = (not _vi.startswith('n')) if vr_default else _vi.startswith('y')
            workflow['verify_roundtrip'] = bool(verify)
            _staged['last_verify_roundtrip'] = bool(verify)
        else:
            verify = False

        # Already-archived sources — asked for EVERY direction, but what stands
        # behind it differs enormously, so the wording follows the direction
        # instead of promising the same thing everywhere.
        conv = workflow.get('conversion_type', '')
        # Only these two can PROVE provenance: the transcoder stores the
        # source's md5 keyed by the output's name, so the hash must match.
        _provable = conv in ('transcode_lossless', 'jxl_to_jpeg_lossless')
        # The output cannot reproduce the source: only the provenance marker
        # ties it to this file (since the 2026-10-08 audit the transcoder's
        # lossy gate requires it too). Still the combination that gets its own
        # confirmation — a marker names a FILE, not its pixels.
        _lossy = conv in ('convert_lossy', 'jxl_to_jpeg_force', 'jxl_to_png',
                          'jxl_to_jpeg_auto')

        if _provable:
            _guarantee = ("the stored checksum must match, which PROVES that output "
                          "came from this exact file.")
        elif verify:
            _guarantee = ("it must carry the provenance marker naming THIS source, and "
                          "also decode back to the source pixels.")
        elif _lossy:
            _guarantee = ("and it must carry the provenance marker naming THIS source — a "
                          "same-named file from anywhere else keeps the original. That is "
                          "ALL: this direction stores no checksum and a lossy output cannot "
                          "be compared with its source.")
        else:
            _guarantee = ("and it must carry the provenance marker naming THIS source — a "
                          "same-named file from anywhere else keeps the original. Nothing "
                          "compares the image contents.")

        ds_default = bool(self.config.config.last_delete_skipped)
        ds_explain = (
            "Sources whose output already exists are reported as SKIP and are normally "
            "KEPT — which means a run interrupted between the conversion and the delete "
            "can never be finished without redoing everything. Turning this on deletes "
            "them too. They are never deleted on the timestamp alone: the output must "
            "exist and be structurally valid, " + _guarantee)
        if RICH_AVAILABLE and console:
            console.print(f"\n[dim]{ds_explain}[/dim]")
            del_skipped = Confirm.ask(
                "[cyan]Also delete originals that were already converted?[/cyan]",
                default=ds_default)
        else:
            print(f"\n{ds_explain}")
            _di = input(f"Also delete originals that were already converted? "
                        f"[{'Y/n' if ds_default else 'y/N'}]: ").strip().lower()
            del_skipped = (not _di.startswith('n')) if ds_default else _di.startswith('y')

        if del_skipped and _lossy:
            # Same gate execute_workflow applies to repeats and presets, asked
            # here so the choice is made while the user is still configuring.
            # The shared helper reads advanced_options, which does not exist yet
            # at this point in the wizard — hand it a view of the decision.
            _probe = dict(workflow)
            _probe['advanced_options'] = {'delete_source': True, 'delete_skipped': True}
            self._confirm_lossy_delete_skipped(_probe)
            del_skipped = bool(_probe['advanced_options'].get('delete_skipped'))
            workflow['_lossy_skip_confirmed'] = True

        workflow['delete_skipped'] = bool(del_skipped)
        _staged['last_delete_skipped'] = bool(del_skipped)

        # How an EXISTING output is matched to the source about to replace it.
        # Only asked where it can bite: the runs that collapse folder structure,
        # where two sources in different folders resolve to the same output.
        # Modes 1/3/8 — and mode 0 in place — derive the output from the source's
        # own folder, so there is nothing to confuse and nothing to ask.
        if collapses:
            # adopt exists only in the encoder (see _supports_provenance_adopt):
            # offering it for the other directions built a command line their
            # script rejects at argparse.
            _adopt_ok = _supports_provenance_adopt(origin, dest)
            _pv_choices = ["path", "content"] + (["adopt"] if _adopt_ok else [])
            pv_child = _child_default(_child_module(origin, dest), 'provenance')
            pv_default = self.config.config.last_provenance or pv_child
            if pv_default not in _pv_choices:
                # e.g. a remembered `adopt` on a direction without it: the
                # child's own setting, or the strict check if that is not
                # offered either.
                pv_default = pv_child if pv_child in _pv_choices else 'path'
            pv_explain = (
                f"{scope_label}, so two files with the same name in "
                f"different folders land on the same output. Before overwriting an output "
                f"that already exists — and deleting the file that made it — the run "
                f"checks the archive really came from this source.\n"
                f"  path    = compares the recorded LOCATION. Instant. Handles re-exporting "
                f"a file in place.\n"
                f"  content = also accepts a matching IMAGE. SLOWER (reads and hashes every "
                f"source), but NECESSARY IF YOU MOVED FOLDERS since archiving.")
            if _adopt_ok:
                pv_explain += (
                    f"\n  adopt   = for an archive made BEFORE this check existed, which has "
                    f"no record at all. Each unrecorded output is VERIFIED against its source "
                    f"and then stamped, so this is a ONE-TIME pass: afterwards the strict "
                    f"check applies again.")
            _pv_list = "/".join(_pv_choices)
            if RICH_AVAILABLE and console:
                console.print(f"\n[dim]{pv_explain}[/dim]")
                provenance = Prompt.ask(
                    "[cyan]Match existing outputs by[/cyan]",
                    choices=_pv_choices, default=pv_default)
            else:
                print(f"\n{pv_explain}")
                _pi = input(f"Match existing outputs by ({_pv_list}) "
                            f"[{pv_default}]: ").strip().lower()
                provenance = _pi if _pi in _pv_choices else pv_default
            workflow['provenance'] = provenance
            _staged['last_provenance'] = provenance
            _staged['last_adopt_scan'] = None
            if provenance == 'content':
                _n = ("Content matching reads every source file — expect the run to take "
                      "noticeably longer.")
                if RICH_AVAILABLE and console:
                    console.print(f"[yellow]{_rich_safe(_n)}[/yellow]")
                else:
                    print(_n)
            elif provenance == 'adopt':
                # The scan is what makes adoption safe rather than a rubber
                # stamp, so it is ON unless the user turns it off knowingly.
                _sc = ("Adoption VERIFIES each unrecorded output against its source before "
                       "trusting it — it decodes every one, so on a large library this is "
                       "SLOW. Turning it off adopts the existing pairing without proof: only "
                       "do that if you know this archive was never built with a mode that "
                       "merges folders (2/4/5/6/7), which is what could have put a different "
                       "photo under that name.")
                if RICH_AVAILABLE and console:
                    console.print(f"\n[dim]{_sc}[/dim]")
                    _scan = Confirm.ask("[cyan]Verify each one before adopting?[/cyan]",
                                        default=True)
                else:
                    print(f"\n{_sc}")
                    _si = input("Verify each one before adopting? [Y/n]: ").strip().lower()
                    _scan = not _si.startswith('n')
                workflow['adopt_scan'] = bool(_scan)
                _staged['last_adopt_scan'] = bool(_scan)
                if not _scan:
                    _w = ("Adopting WITHOUT verification. Every existing output will be "
                          "trusted as belonging to the source that shares its name.")
                    if RICH_AVAILABLE and console:
                        console.print(f"[yellow]{_rich_safe(_w)}[/yellow]")
                    else:
                        print(_w)
        if del_skipped and not verify and not _provable:
            _w = ("Deleting already-converted originals with no content check: what "
                  "stands between a stale output and your original is the provenance "
                  "marker (it names the source FILE, not its pixels) and a structural "
                  "check.")
            if RICH_AVAILABLE and console:
                console.print(f"[yellow]{_rich_safe(_w)}[/yellow]")
            else:
                print(_w)

        # Hand the staged answers to the wizard result; main() commits them to
        # the config next to save_last_session, so a cancelled wizard leaves
        # saved state untouched (see the top of this function).
        if _staged:
            workflow.setdefault('_pending_session_fields', {}).update(_staged)

    def _wizard_select_mode_manual(self, workflow: Dict) -> bool:
        """Manual mode selection (called after declining auto recommendation)."""
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        export_marker = self.config.config.export_marker

        m1_name, m3_name = _dest_folder_names(origin, dest)
        # Same differentiation as the Step-4 menu above: JXL->JXL in-place
        # REPLACES the sources, it does not sit "side by side" with them.
        if origin == 'jxl' and dest == 'jxl':
            m0_desc = "Replaces each source JXL in place (originals NOT kept)"
            m8_desc = "Replaces every source JXL in place, all subfolders (originals NOT kept)"
        else:
            m0_desc = f"{origin.upper()} and {dest.upper()} side by side"
            m8_desc = "Output next to each source, all subfolders"
        modes = [
            ("0", "In-place", m0_desc),
            ("1", "Subfolder", f"Creates '{m1_name}' subfolder"),
            ("2", "Flat", "All to one folder (recursive)"),
            ("3", "Recursive", f"'{m3_name}' in each subfolder"),
            ("4", "Rename", f"Renames folder {origin}→{dest}"),
            ("5", "Sibling", f"Creates sibling folder next to source"),
            ("6", f"Marker {export_marker} (full)", f"Only inside folders matching '{export_marker}'"),
            ("7", f"Marker {export_marker} (subfolder)", f"Only one subfolder of '{export_marker}'"),
            ("8", "In-place recursive", m8_desc),
        ]

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Select Mode Manually[/bold cyan]")
            for key, name, desc in modes:
                console.print(f"[{key}] [bold green]{name}[/bold green] — {desc}")
            console.print(self._delete_entry_line())

            valid_choices = ([m[0] for m in modes]
                             + [self._DELETE_CHOICE, self._DELETE_CHOICE.lower()])
            while True:
                choice = Prompt.ask("Select mode", choices=valid_choices)
                if choice:
                    choice = choice.upper()
                    break
        else:
            print("\n--- Select Mode Manually ---")
            for key, name, desc in modes:
                print(f"[{key}] {name} — {desc}")
            print(self._delete_entry_line(plain=True))
            valid_choices = [m[0] for m in modes] + [self._DELETE_CHOICE]
            choice = input(f"Select ({'/'.join(valid_choices)}): ").strip().upper()
            while choice not in valid_choices:
                choice = input(f"Select ({'/'.join(valid_choices)}): ").strip().upper()

        _handled = self._handle_mode_choice(workflow, choice, modes)
        if _handled is not None:
            return _handled
        workflow['mode'] = int(choice)
        return True

    def _show_mode_details_and_select(self, workflow: Dict) -> bool:
        """Display detailed explanation of all modes and let user select."""
        origin = workflow['origin_format']
        dest = workflow['dest_format']
        export_marker = self.config.config.export_marker

        if origin == 'jxl' and dest == 'jxl':
            # The recompressor has no "side by side": same extension in, same
            # extension out means the output REPLACES the source file. Saying
            # "stay side by side" here hid the most destructive default the
            # wizard offers.
            inplace_desc = (
                "REPLACES each source JXL with the recompressed one, in the SAME\n"
                "folder. The original bytes cannot be recovered afterwards.\n"
                "[bold red]Input file/folder determines output location.[/bold red]\n"
                "Single file -> same folder; folder -> every file replaced in place.\n"
                "[bold green]Non-recursive[/bold green] - subfolders are NOT processed.")
            inplace_rec_desc = (
                "Like mode 0, RECURSIVE: walks every subfolder and REPLACES each\n"
                "source JXL with its recompressed version, in place. The original\n"
                "bytes cannot be recovered afterwards.")
        else:
            inplace_desc = (
                f"{origin.upper()} and {dest.upper()} stay side by side in the SAME folder.\n"
                f"Input file/folder determines output location.\n"
                f"Single file -> same folder; folder -> flat output in that folder.\n"
                f"[bold green]Non-recursive[/bold green] - subfolders are NOT processed.")
            inplace_rec_desc = (
                "Like mode 0, but RECURSIVE: walks every subfolder and writes each output\n"
                "next to its source.\n"
                "[bold green]Your originals are KEPT.[/bold green] Deleting them is [bold red][D][/bold red], "
                "below — it is not a mode.")

        details = [
            ("0", "In-place", inplace_desc),

            ("1", "Subfolder",
             f"Creates a [green]'{_dest_folder_names(origin, dest)[0]}'[/green] subfolder next to each source folder.\n"
             f"Example: [cyan]F:/Photos/2024/[/cyan] -> [cyan]F:/Photos/2024/{_dest_folder_names(origin, dest)[0]}/[/cyan]\n"
             f"Works on folder input only. Non-recursive."),

            ("2", "Flat -> output folder",
             f"All {origin.upper()} files from ALL subfolders are merged into a single output folder.\n"
             f"Fully recursive — every subfolder is scanned.\n"
             f"If you specify an output root, files land there.\n"
             f"Otherwise uses the input root as output root."),

            ("3", "Recursive subfolders",
             f"Each subfolder gets its own [green]'{_dest_folder_names(origin, dest)[1]}'[/green] subfolder.\n"
             f"Preserves folder structure: [cyan]F:/Photos/2024/A/[/cyan] -> [cyan]F:/Photos/2024/A/{_dest_folder_names(origin, dest)[1]}/[/cyan]"),

            ("4", "Folder rename (suffix swap)",
             f"Renames the folder, replacing [green]{origin.upper()}[/green] with [green]{dest.upper()}[/green] in the folder name.\n"
             f"[cyan]F:/Photos/JXL_raw/[/cyan] -> [cyan]F:/Photos/{dest.upper()}_raw/[/cyan]\n"
             f"If {origin.upper()} is not found, appends {dest.upper()} and logs a warning."),

            ("5", "Sibling folder",
             f"Creates a sibling folder next to each source folder, without renaming it.\n"
             f"[cyan]F:/Photos/Raw/[/cyan] -> [cyan]F:/Photos/JXL_16bits/[/cyan] (or TIFF_16bits / JXL_jpeg / JPEG_recovered, by direction)"),

            ("6", f"Marker {export_marker} (full)",
             f"ONLY processes files INSIDE folders matching '{export_marker}' (prefix/suffix token).\n"
             f"Recursively finds ALL matching folders and processes everything under each.\n"
             f"Ignores ALL files outside matching folders.\n"
             f"Works with _EXPORT, Export_Lightroom, Lightroom_Export, etc. (case-insensitive)."),

            ("7", f"Marker {export_marker} (specific subfolder)",
             f"Like mode 6, but ONLY processes files inside one subfolder of the export marker.\n"
             f"The subfolder name is asked in Step 5 (empty = all subfolders = same as mode 6).\n"
             f"Files in other subfolders within export folders are ignored.\n"
             f"Use when you keep different color-space variants in separate subfolders."),

            ("8", "In-place recursive", inplace_rec_desc),

            ("D", "Convert and DELETE the originals",
             f"[bold red]IRREVERSIBLE.[/bold red] Not a layout: you pick any mode 0-8 above, and the\n"
             f"original {origin.upper()} files are removed once each output has been written to\n"
             f"that mode's destination and verified.\n"
             f"You are asked to confirm, then shown exactly how many files from which\n"
             f"folder, then asked for the current time (HHMM). A dry run never deletes."),
        ]

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]--- Mode Detailed Explanations ---[/bold cyan]\n")
            for key, name, desc in details:
                # Red marks what actually destroys data — which is [D], not
                # mode 8. Mode 8 keeps your files.
                style = "red" if key == self._DELETE_CHOICE else "blue"
                console.print(f"[{key}] [bold {style}]{name}[/bold {style}]")
                console.print(Panel.fit(desc, border_style=style))
                console.print()

            valid_choices = [d[0] for d in details] + ["A", "M", "a", "m",
                                                       self._DELETE_CHOICE.lower()]
            while True:
                choice = Prompt.ask("Select mode", choices=valid_choices)
                if choice in valid_choices:
                    choice = choice.upper()
                    break
        else:
            print("\n=== Mode Detailed Explanations ===\n")
            for key, name, desc in details:
                print(f"[{key}] {name}")
                clean = (desc.replace("[bold green]", "").replace("[/bold green]", "")
                         .replace("[bold red]", "").replace("[/bold red]", "")
                         .replace("[bold blue]", "").replace("[/bold blue]", "")
                         .replace("[bold cyan]", "").replace("[/bold cyan]", "")
                         .replace("[bold yellow]", "").replace("[/bold yellow]", "")
                         .replace("[bold ", "").replace("[/bold]", "")
                         .replace("[green]", "").replace("[/green]", "")
                         .replace("[cyan]", "").replace("[/cyan]", "")
                         .replace("[red]", "").replace("[/red]", "")
                         .replace("[yellow]", "").replace("[/yellow]", "")
                         .replace("[dim]", "").replace("[/dim]", ""))
                print(f"   {clean}\n")
            valid_choices = [d[0] for d in details] + ["A", "M"]
            choice = input("Select mode [0-8/D/A/M]: ").strip().upper()
            while choice not in valid_choices:
                choice = input("Select mode [0-8/D/A/M]: ").strip().upper()

        # Handle Auto and Manifest from detail view
        if choice == "A":
            return self._wizard_auto_mode(workflow)

        if choice == "M":
            return self._wizard_run_from_manifest(workflow)

        # [D] is in `details` (so it is explained here too), which means it is
        # already in valid_choices — but it still has to be RESOLVED, or int()
        # below would raise on it.
        _modes = [(k, n, "") for k, n, _d in details if k != self._DELETE_CHOICE]
        _handled = self._handle_mode_choice(workflow, choice, _modes)
        if _handled is not None:
            return _handled

        workflow['mode'] = int(choice)
        return True

    def _wizard_mode_specific_config(self, workflow: Dict) -> bool:
        """Step 5: Mode-specific configuration"""
        mode = workflow['mode']
        # Preserve anything the Auto Mode already seeded (export_subfolder
        # detection for mode 7) — a fresh dict would silently drop it and the
        # run would behave like mode 6 while the preview said mode 7.
        mode_config = dict(workflow.get('mode_config') or {})
        origin = workflow['origin_format']
        dest = workflow['dest_format']

        # Manifest mode (99) doesn't need mode-specific config
        if mode == 99:
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Manifest Mode[/bold cyan]")
                console.print(f"[dim]Running {len(workflow.get('manifest_entries', []))} entries from manifest[/dim]")
            else:
                print(f"\n--- Step 5: Manifest Mode ---")
            workflow['mode_config'] = mode_config
            return True

        if mode in [6, 7]:
            current = self.config.config.export_marker
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Marker Configuration[/bold cyan]")
                console.print(f"Current: [green]{_rich_safe(current)}[/green]")
                new_marker = Prompt.ask("EXPORT marker", default=current)
            else:
                print(f"\n--- Step 5: Marker Configuration ---")
                print(f"Current: {current}")
                new_marker = input(f"EXPORT marker [{current}]: ").strip() or current

            if new_marker != current:
                # Store per-workflow only (persisted at the end via mode_config);
                # the global setting is NOT mutated mid-wizard.
                mode_config['export_marker'] = new_marker

            if mode == 7:
                # Mode 7 processes only one subfolder of the export marker.
                # Empty = all subfolders (identical to mode 6). Default comes
                # from this workflow's config (auto-mode detection) first,
                # then the last saved session.
                last_sub = mode_config.get('export_subfolder') or \
                    (self.config.config.last_mode_config or {}).get('export_subfolder', '')
                if RICH_AVAILABLE and console:
                    sub = Prompt.ask(
                        "Subfolder inside export marker to process (empty = all, same as mode 6)",
                        default=last_sub
                    )
                else:
                    sub = input(f"Subfolder inside export marker (empty = all) [{last_sub}]: ").strip() or last_sub
                sub = _strip_surrounding_quotes(sub).strip().strip('/\\')
                if sub:
                    mode_config['export_subfolder'] = sub
                    if workflow.get('auto_mode_used'):
                        console_msg = "[yellow]Note: subfolder set manually after Auto Mode — preview used auto-detection.[/yellow]"
                        if RICH_AVAILABLE and console:
                            console.print(console_msg)
                        else:
                            print("Note: subfolder set manually after Auto Mode — preview used auto-detection.")
                else:
                    # Empty answer = "all subfolders": drop any seeded value so
                    # the intent is explicit, not inherited.
                    mode_config.pop('export_subfolder', None)

            # The modes 6/7 output folder. Only the two directions whose child
            # accepts --export-jxl-folder offer it; the child validates the
            # name itself (one plain component, not the marker, not the input
            # subfolder) and exits 2 with a clear message the wrapper shows.
            if dest == 'jxl' and origin in ('tiff', 'jxl'):
                _script_default = _export_folder_name(origin, dest)
                _folder_default = (mode_config.get('export_jxl_folder')
                                   or (self.config.config.last_mode_config or {}).get('export_jxl_folder')
                                   or _script_default)
                if RICH_AVAILABLE and console:
                    _folder = Prompt.ask("Output folder name under the marker",
                                         default=_folder_default)
                else:
                    _folder = input(f"Output folder name under the marker "
                                    f"[{_folder_default}]: ").strip() or _folder_default
                _folder = _strip_surrounding_quotes(_folder).strip().strip('/\\')
                if _folder == _script_default:
                    mode_config.pop('export_jxl_folder', None)
                else:
                    mode_config['export_jxl_folder'] = _folder

        elif mode == 2:
            default_out = Path(workflow['input_dir']).parent / "output"
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Flat Folder[/bold cyan]")
                output_dir = Prompt.ask("Output directory", default=str(default_out))
            else:
                print(f"\n--- Step 5: Flat Folder ---")
                output_dir = input(f"Destination [{default_out}]: ").strip() or str(default_out)
            mode_config['output_dir'] = _strip_surrounding_quotes(output_dir)

        elif mode == 1:
            folder_name = _dest_folder_names(origin, dest)[0]
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Subfolder Name[/bold cyan]")
                console.print(f"Will create: [green]'{_rich_safe(folder_name)}'[/green] in each source folder")
            else:
                print(f"\n--- Step 5: Subfolder Name ---")
                print(f"Will create: '{folder_name}' in each source folder")

        elif mode == 3:
            folder_name = _dest_folder_names(origin, dest)[1]
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Subfolder Name[/bold cyan]")
                console.print(f"Will create: [green]'{_rich_safe(folder_name)}'[/green] in each source folder")
            else:
                print(f"\n--- Step 5: Subfolder Name ---")
                print(f"Will create: '{folder_name}' in each source folder")

        elif mode in [4, 5]:
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Rename Configuration[/bold cyan]")
                console.print(f"Example: folder_{origin} → [green]folder_{dest}[/green]")
            else:
                print(f"\n--- Step 5: Rename Configuration ---")
                print(f"Example: folder_{origin} → folder_{dest}")

        else:
            if RICH_AVAILABLE and console:
                console.print(f"\n[bold cyan]Step 5: Confirmation[/bold cyan]")
                console.print(f"Mode {mode} - OK")
            else:
                print(f"\n--- Step 5: Confirmation ---")

        workflow['mode_config'] = mode_config
        return True

    def _confirm_hhmm(self, headline: str, body: str) -> bool:
        """The HHMM proof-of-presence prompt, shared by every destructive gate."""
        from datetime import datetime
        now = datetime.now()
        token = now.strftime("%H%M")

        if RICH_AVAILABLE and console:
            console.print(f"[bold red]{headline}[/bold red]")
            console.print(f"[red]{body}[/red]")
            console.print(f"Enter current time ({token}) to confirm:")
        else:
            print(f"\n{headline}")
            print(f"⚠️  {body}")
            print(f"Enter {token} to confirm:")

        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            user_input = ""

        if user_input != token:
            self._print_error("Confirmation failed!")
            return False

        self._print_success("Confirmed!")
        return True

    def _confirm_archive_mode(self) -> bool:
        return self._confirm_hhmm(
            "⚠️  DELETE ORIGINALS MODE",
            "Original files will be DELETED")

    def _confirm_in_place_replace(self) -> bool:
        return self._confirm_hhmm(
            "⚠️  IN-PLACE REPLACEMENT",
            "Source JXLs will be REPLACED by the recompressed versions — the "
            "original bytes cannot be recovered afterwards")

    def _multipage_summary(self, workflow: Dict):
        """Return (label, is_warning) describing what happens to multi-page TIFFs.

        The question lives inside Advanced Options (which defaults to No), so a
        straight-through run never sees it — this line is the last chance to say
        what happens to extra pages before the user types YES. The default is
        `split` (was `ignore`, which silently dropped them), so only the
        page-dropping choices are flagged as warnings.
        """
        adv = workflow.get('advanced_options', {})
        mp = adv.get('multipage_mode') or _child_default('jxl_tiff_encoder', 'multipage_mode')
        if mp == 'ignore':
            return "ignore — extra pages are DISCARDED (choose split to keep them)", True
        if mp == 'skip':
            return "skip — multi-page TIFFs are NOT converted at all", True
        if mp == 'split_all':
            # split_all never consults thumbnail_mode; saying otherwise implied
            # a choice the encoder ignores.
            return "split_all — one JXL per page, thumbnail pages included", False
        tm = adv.get('thumbnail_mode') or _child_default('jxl_tiff_encoder', 'thumbnail_mode')
        tm_note = {
            'include': "thumbnail pages included",
            'auto': "thumbnail pages kept, except Capture One previews",
            'scans': "thumbnail pages kept for scans only",
        }.get(tm, "thumbnail pages excluded")
        return f"split — one JXL per page ({tm_note})", False

    def _execute_manifest_workflow(self, workflow: Dict, status: Dict[str, bool]) -> bool:
        """Execute workflow from manifest entries."""
        manifest_entries = workflow.get('manifest_entries', [])
        if not manifest_entries:
            self._print_error("No manifest entries found!")
            return False

        origin = workflow['origin_format']
        dest = workflow['dest_format']
        workers = workflow['workers']
        advanced = workflow.get('advanced_options', {})
        dry_run = workflow.get('dry_run', False)

        # The Destination column is only honored by modes 0 and 2 — every other
        # mode computes its own output location from constants (_EXPORT/16B_JXL,
        # converted_jxl/, ...). Modes 6/7 generate Destination == Source, which
        # is why they need the warning even without a divergence.
        ignored_dests = [(s, d, m) for s, d, m in manifest_entries
                         if (m not in (0, 2) and d and Path(d) != Path(s))
                         or m in (6, 7)]
        if ignored_dests:
            warn = (f"Note: {len(ignored_dests)} manifest entry(ies) use modes that ignore the "
                    f"Destination column (only modes 0/2 honor it) — outputs follow the mode rules.")
            if RICH_AVAILABLE and console:
                console.print(f"[yellow]{_rich_safe(warn)}[/yellow]")
            else:
                print(f"WARNING: {warn}")
            for s, d, m in ignored_dests[:5]:
                print(f"  mode {m}: {d} (ignored)")

        # Determine which script to use
        if origin == 'tiff' and dest == 'jxl':
            script = 'jxl_tiff_encoder.py'
        elif origin == 'jxl' and dest == 'tiff':
            script = 'jxl_tiff_decoder.py'
        elif origin == 'jxl' and dest == 'jxl':
            script = 'jxl_recompressor.py'
        else:
            script = 'jxl_jpeg_transcoder.py'

        # Resolve script path relative to SCRIPT_DIR (not CWD)
        script_path = SCRIPT_DIR / script
        if not script_path.exists():
            self._print_error(f"Script not found: {script_path}")
            return False
        script = str(script_path)

        # Per-row effective options: a manifest option column overrides the
        # wizard's answer for THAT row (empty cell = not applied; absent
        # column = the wizard's answer). Computed once, aligned with
        # manifest_entries, and reused by the guards, the collision scan and
        # the command builder.
        _row_opts_all = (workflow.get('manifest_row_options')
                         or [None] * len(manifest_entries))
        _row_effs = [_row_effective_options(workflow, advanced, _o, origin, dest)
                     for _o in _row_opts_all]

        def _row_export_cfg(wf_row):
            """(marker, subfolder, jxl_folder) this row's child will run with."""
            mc = wf_row.get('mode_config') or {}
            return (mc.get('export_marker') or self.config.config.export_marker,
                    mc.get('export_subfolder'),
                    mc.get('export_jxl_folder'))

        # Create the analyzer once — prefer the workflow's mode_config marker (a
        # manifest run with a custom marker must detect modes with the same
        # marker the children will use). `analyzer` is also used by the
        # collision scan below (_get_extensions), so it stays even though the
        # per-row detection uses a FolderAnalyzer per row (its __init__ only
        # stores values, and a row's ExportMarker column must win here).
        _marker = workflow.get('mode_config', {}).get('export_marker') or self.config.config.export_marker
        analyzer = FolderAnalyzer(Path("."), origin, dest, _marker)
        _row_markers = [_row_export_cfg(_w)[0] for _w, _a in _row_effs]

        # Resolve every entry's mode ONCE, here, and hand the guards below the
        # same modes the children will actually run with. A legacy manifest (no
        # Mode cell) arrives as None and is resolved to 0/6/7 by
        # detect_mode_for_entry — but the guards used to receive the raw None
        # and fall back to a flat Destination scan, which models a write that
        # modes 6/7 never perform. Fail-closed behaviour is unchanged: an entry
        # that still resolves to None keeps counting as "unknown, assume the
        # worst" inside each guard.
        resolved_entries = [
            (source, dest_path,
             FolderAnalyzer(Path("."), origin, dest, _row_markers[_i])
                 .detect_mode_for_entry(source, dest_path, original_mode=entry_mode))
            for _i, (source, dest_path, entry_mode) in enumerate(manifest_entries)
        ]

        # The derivative rules per ROW (_GATE_RULES, path "manifest"), before
        # anything is charged or written.
        if _job_gates(self, workflow, "manifest", origin=origin, dest=dest,
                      advanced=advanced, row_effs=_row_effs,
                      resolved_entries=resolved_entries):
            return False
        if (origin == 'jxl' and dest in ('jpeg', 'png')
                and workflow.get('conversion_type') == 'jxl_to_jpeg_lossless'):
            # A bit-exact JPEG reconstruction cannot be resized/sharpened; the
            # child would refuse per file — refuse here, before the token.
            _bad = [i + 1 for i, (_w, _a) in enumerate(_row_effs)
                    if _a.get('resize_mode') or (_a.get('sharpen') or 'none') != 'none']
            if _bad:
                self._print_error("A bit-exact JPEG reconstruction cannot be "
                                  "resized/sharpened — pick the lossy JXL→JPEG "
                                  "conversion for that "
                                  f"(row(s): {', '.join(map(str, _bad))}).")
                return False
            # --rename-from is just as impossible: the recovered JPEG keeps
            # the ORIGINAL file name (the child ignores the flag on the
            # lossless path), so a rename would only fool the collision scan
            # into modelling outputs the run never writes. Refuse it too.
            _ren = [i + 1 for i, (_w, _a) in enumerate(_row_effs)
                    if _a.get('rename_from')]
            if _ren:
                self._print_error("A bit-exact JPEG reconstruction keeps the "
                                  "original file names — --rename-from does not "
                                  "apply to the lossless JXL→JPEG recovery "
                                  f"(row(s): {', '.join(map(str, _ren))}). Pick "
                                  "the lossy conversion for that.")
                return False
            # OutputICC is likewise ignored on the lossless path (there is no
            # pixel to convert) — warn, don't refuse: the recovery itself is
            # still exactly what was asked.
            _icc = [i + 1 for i, (_w, _a) in enumerate(_row_effs)
                    if _a.get('output_icc')]
            if _icc:
                _msg = (f"OutputICC is ignored by a bit-exact JPEG reconstruction "
                        f"(row(s): {', '.join(map(str, _icc))}) — the recovered "
                        "JPEG keeps the original bytes, colour included.")
                if RICH_AVAILABLE and console:
                    console.print(f"[yellow]{_rich_safe(_msg)}[/yellow]")
                else:
                    print(f"WARNING: {_msg}")

        # A mode-7 entry means "only <marker>/<subfolder>", and the subfolder
        # reaches the children as --export-subfolder — which comes from
        # mode_config. On a mode-99 (manifest) run it USED to be always empty;
        # the item-19 fix now preserves export_marker/export_subfolder in the
        # workflow, so a manifest recorded with a subfolder arrives with it
        # set and skips this derivation entirely. Auto-generated manifests
        # bake the subfolder into the Source itself (<marker>/<sub>), so it
        # can also be derived back — and SHOULD be passed explicitly, because
        # a child whose EXPORT_*_SUBFOLDER constant was edited would otherwise
        # filter the entry by the wrong name. Entries naming DIFFERENT
        # subfolders need no flag at all: each Source already scopes its own
        # child process. The one dangerous shape is a HAND-WRITTEN entry with
        # Mode=7 and a Source ABOVE the marker: with an empty subfolder its
        # child converts EVERY subfolder of the marker — mode 6 wearing a
        # mode-7 label.
        if (any(m == 7 for _, _, m in resolved_entries)
                and not (workflow.get('mode_config') or {}).get('export_subfolder')):
            derived = set()
            underivable = []
            for i, (_s, _d, _m) in enumerate(resolved_entries):
                if _m != 7:
                    continue
                # Rows with their own ExportSubfolder keep it: they neither
                # participate in the derivation nor earn the warning.
                if (_row_opts_all[i] or {}).get('export_subfolder'):
                    continue
                _parts = Path(_s).parts
                # The deepest match: the children's anchor for this Source
                # (_marker_anchor_index, E-H).
                _idx = next((j for j in range(len(_parts) - 1, -1, -1)
                             if _marker_matches(_parts[j].lower(),
                                                _row_markers[i].lower())), None)
                if _idx is not None and _idx + 1 < len(_parts):
                    derived.add(_parts[_idx + 1])
                else:
                    underivable.append(_s)
            if underivable:
                _sub_warn = (
                    "This manifest has mode-7 entries whose subfolder cannot be "
                    "determined (the Source is not <marker>/<subfolder>). The "
                    "children will use their script's EXPORT_*_SUBFOLDER default "
                    "— and when that is empty, they process EVERY subfolder of "
                    "the export marker: they will run as mode 6. Filling the "
                    "ExportSubfolder column on those rows resolves this.")
                if RICH_AVAILABLE and console:
                    console.print(f"[yellow]{_rich_safe(_sub_warn)}[/yellow]")
                else:
                    print(f"WARNING: {_sub_warn}")
                for _s in underivable[:5]:
                    print(f"  mode 7, no subfolder in Source: {_s}")
                if workflow.get('unattended'):
                    # Fail closed, like the overlap guard: nobody is there to
                    # notice the run is converting more than the manifest says.
                    self._print_error(
                        "Refusing to run unattended: write the mode-7 Sources as "
                        "<marker>/<subfolder>, fill the ExportSubfolder column on "
                        "those rows, or use mode 6 if every subfolder is intended.")
                    return False
                if RICH_AVAILABLE and console:
                    ok_sub = Confirm.ask("Run anyway?", default=False)
                else:
                    ok_sub = input("Run anyway? [y/N]: ").strip().lower().startswith('y')
                if not ok_sub:
                    return False
            elif len(derived) == 1:
                _sub = derived.pop()
                workflow.setdefault('mode_config', {})['export_subfolder'] = _sub
                _sub_msg = (f"Mode-7 entries: processing only subfolder {_sub!r} "
                            f"(derived from the manifest's Source paths).")
                if RICH_AVAILABLE and console:
                    console.print(f"[dim]{_rich_safe(_sub_msg)}[/dim]")
                else:
                    print(_sub_msg)

        # The mode-7 derivation above may have filled the run's export_subfolder.
        # Rows with their own Export* columns hold a COPY of mode_config, so
        # they would not see it — rebuild, so every row inherits the derived
        # subfolder unless it has its own. (_row_export can only be computed
        # here, for the same reason.)
        _row_effs = [_row_effective_options(workflow, advanced, _o, origin, dest)
                     for _o in _row_opts_all]
        _row_export = [_row_export_cfg(_w) for _w, _a in _row_effs]

        # The child validates the modes 6/7 output folder name at startup
        # (argparse exit 2). Mirror that here, per row and with the row's
        # EFFECTIVE trio (so the default name is checked against the row's
        # marker too), so a bad name fails before the first entry instead of
        # on entry N mid-run.
        if (origin, dest) in (('tiff', 'jxl'), ('jxl', 'jxl')):
            try:
                if origin == 'tiff':
                    import jxl_tiff_encoder as _vchild
                else:
                    import jxl_recompressor as _vchild
            except ImportError:
                _vchild = None      # the child validates it itself (exit 2)
            if _vchild is not None:
                _bad_folder = []
                for i, (_s, _d, _m) in enumerate(resolved_entries, 1):
                    if _m not in (6, 7):
                        continue
                    _mk, _sub, _jf = _row_export[i - 1]
                    _why = _vchild._validate_export_folder_name(
                        _jf or _vchild.EXPORT_JXL_FOLDER, _mk,
                        (_sub or "") if _m == 7 else "")
                    if _why:
                        _bad_folder.append((i, _why))
                if _bad_folder:
                    for i, _why in _bad_folder[:5]:
                        self._print_error(f"Manifest row {i}: output folder — {_why}")
                    return False

        # Overlapping source trees: one entry's Source inside another's means
        # the same files are processed twice by SEPARATE child processes. Some
        # users do this on purpose (e.g. E:\Fotos in mode 6 + E:\Fotos\2024_EXPORT
        # in mode 0 — different outputs, no conflict), so attended runs get a
        # loud warning and a choice; unattended presets are refused (fail
        # closed, like the delete gates).
        overlaps = self._manifest_source_overlaps(resolved_entries)
        if overlaps:
            head = (f"{len(overlaps)} manifest entry pair(s) have duplicate or nested "
                    f"Source folders — the same files would be processed twice:")
            if RICH_AVAILABLE and console:
                console.print(f"[yellow]{_rich_safe(head)}[/yellow]")
            else:
                print(f"WARNING: {head}")
            for a, b in overlaps[:10]:
                print(f"  {a}  <->  {b}")
            if workflow.get('unattended'):
                self._print_error(
                    "Refusing to run unattended: split the manifest so each folder "
                    "is covered by exactly one entry, or run it from the menu.")
                return False
            if RICH_AVAILABLE and console:
                ok_overlap = Confirm.ask("Run anyway?", default=False)
            else:
                ok_overlap = input("Run anyway? [y/N]: ").strip().lower().startswith('y')
            if not ok_overlap:
                return False

        # Cross-entry duplicate outputs: each entry is a separate child process,
        # so no child can see a collision that spans two entries. Refuse loudly
        # instead of silently archiving only one of the two sources.
        # The scan itself walks every Source recursively and can take minutes on
        # large trees, so it is skipped when collisions are IMPOSSIBLE — see
        # _manifest_needs_collision_scan for exactly when that holds.
        # The same walk also records each entry's resolved output folders
        # (cross_sink) for the output-x-Source check below; when the scan is
        # skipped the cross case is impossible too — every entry then writes
        # only inside its own Source tree, so another entry's Source sitting
        # there would already be a Source overlap, which the gate refuses to
        # declare safe.
        cross_out_folders: Dict[int, Set[str]] = {}
        # Each entry's marker comes from its own row (its ExportMarker column,
        # or the run's), and the check reasons per row too: disjoint trees under
        # markers that cannot nest stay skippable, while nested marker dirs
        # force the scan.
        _can_skip = not self._manifest_needs_collision_scan(
            resolved_entries, _marker,
            row_markers=[_m for _m, _s, _j in _row_export])
        if _can_skip:
            _skip_msg = ("Collision check: skipped (no two entries can share an "
                         "output folder).")
            if RICH_AVAILABLE and console:
                console.print(f"[dim]{_rich_safe(_skip_msg)}[/dim]")
            else:
                print(_skip_msg)
            collisions = []
        else:
            collisions = self._manifest_output_collisions(
                resolved_entries, analyzer._get_extensions(origin),
                origin=origin, dest=dest,
                export_marker=_marker,
                export_subfolder=workflow.get('mode_config', {}).get('export_subfolder'),
                export_jxl_folder=workflow.get('mode_config', {}).get('export_jxl_folder'),
                rename_from=(workflow.get('advanced_options') or {}).get('rename_from', ''),
                rename_to=(workflow.get('advanced_options') or {}).get('rename_to', ''),
                row_renames=[((_a.get('rename_from') or ''),
                              (_a.get('rename_to') or ''))
                             for _w, _a in _row_effs],
                row_export=_row_export,
                row_excludes=[_w.get('exclude_folders') for _w, _a in _row_effs],
                cross_sink=cross_out_folders,
                conversion_type=workflow.get('conversion_type'),
                page_options=(workflow.get('advanced_options') or {}),
            )
        if collisions:
            self._print_error(
                "Aborting: manifest entries would write different files to the same output."
            )
            for a, b, d in collisions[:10]:
                msg = f"  {a} + {b}  ->  same name in {d}"
                if RICH_AVAILABLE and console:
                    console.print(f"[red]{_rich_safe(msg)}[/red]")
                else:
                    print(msg)
            if len(collisions) > 10:
                print(f"  ... and {len(collisions) - 10} more")
            self._print_error(
                "Rename the inputs, pick another mode, or run the folder directly "
                "(a non-manifest run detects this itself)."
            )
            return False

        # Cross-entry output x Source: one entry's output folder is another
        # entry's Source — the folder often does not exist yet (the first entry
        # creates it during the run), so neither the output-x-output scan nor
        # the Source-x-Source overlap above can see it, and a disk-level check
        # cannot either. Entry B would then process the files A just created:
        # for JXL->JXL that is a lost generation; for every direction it is a
        # silent dependency on another entry's success. Chaining on purpose is
        # legitimate, so this follows the Source-overlap pattern: loud warning,
        # unattended runs refused (fail closed), attended runs may continue.
        cross_collisions = self._manifest_output_source_collisions(
            resolved_entries, cross_out_folders)
        if cross_collisions:
            head = (f"{len(cross_collisions)} manifest entry pair(s) chain outputs into "
                    f"inputs — one entry's OUTPUT folder is another entry's SOURCE:")
            if RICH_AVAILABLE and console:
                console.print(f"[yellow]{_rich_safe(head)}[/yellow]")
            else:
                print(f"WARNING: {head}")
            for src_a, src_b, out in cross_collisions[:10]:
                msg = (f"  output of [{self._truncate_path(src_a, 30)}] -> "
                       f"{self._truncate_path(out, 30)}  ==  Source of "
                       f"[{self._truncate_path(src_b, 30)}]")
                if RICH_AVAILABLE and console:
                    console.print(f"[yellow]{_rich_safe(msg)}[/yellow]")
                else:
                    print(msg)
            if len(cross_collisions) > 10:
                print(f"  ... and {len(cross_collisions) - 10} more")
            if workflow.get('unattended'):
                self._print_error(
                    "Refusing to run unattended: this manifest reads folders that "
                    "another entry of the same run writes. Run it from the menu, or "
                    "split it into separate runs.")
                return False
            if RICH_AVAILABLE and console:
                ok_cross = Confirm.ask("Run anyway?", default=False)
            else:
                ok_cross = input("Run anyway? [y/N]: ").strip().lower().startswith('y')
            if not ok_cross:
                return False

        # delete_source gets --delete-confirm-off from the cmd builder — so the
        # wrapper MUST charge the HHMM token itself, or nothing asks at all.
        #
        # NOT keyed on mode 8. The cmd builder appends --delete-source for ANY
        # mode, so the mode test only ever held because the children then
        # ignored it outside mode 8. Now that they honour it everywhere, a
        # mode-3 manifest with delete_source would have deleted originals with
        # no confirmation anywhere in the chain.
        # Lossy + delete_skipped: the one combination with no provenance of any
        # kind. (Only the lossy TRANSCODER directions, `_LOSSY_CONVERSIONS`: the
        # recompressor is not among them, and it now proves provenance on its
        # skipped path via the jxlphoto-src markers.) execute_workflow asks this
        # BEFORE dispatching mode 99, so the
        # manifest path never reached it and the extra confirmation vanished for
        # exactly the runs that touch the most files. The gate is idempotent
        # (_lossy_skip_confirmed), so asking here cannot double up.
        # (It turns delete_skipped off in place, in the very dict `advanced`
        # already points at, so the cmd builder below sees the decision.)
        self._confirm_lossy_delete_skipped(workflow)

        # JXL -> JXL in-place entries (mode 0/8, no Destination) REPLACE the
        # source files — the cmd builder passes --delete-confirm-off for
        # those, so the wrapper MUST charge the HHMM token itself, exactly
        # like the delete case.
        _any_in_place_recompress = (
            origin == 'jxl' and dest == 'jxl'
            and any(_recompress_entry_in_place(_s, dest_path, detected_mode)
                    for _s, dest_path, detected_mode in resolved_entries))

        # ...and nobody can type the HHMM token in a scheduled run. The
        # _run_saved_session gate keys on last_mode (0/8) and misses manifest
        # presets, which store 99 — so the refusal has to happen here, where the
        # resolved entries (and their real modes) are known.
        if _any_in_place_recompress and workflow.get('unattended') and not dry_run:
            self._print_error(
                "Refusing to run unattended: this manifest recompresses JXLs in "
                "place (mode 0/8), and the HHMM confirmation cannot be given "
                "here. Run it from the menu, or add --dry-run to simulate it.")
            return False

        if not dry_run and (_flags_request_delete(workflow.get('expert_flags'))
                            or advanced.get('delete_source')
                            or _any_in_place_recompress):
            if _any_in_place_recompress and not (
                    _flags_request_delete(workflow.get('expert_flags'))
                    or advanced.get('delete_source')):
                _confirmed = self._confirm_in_place_replace()
            else:
                _confirmed = self._confirm_archive_mode()
            if not _confirmed:
                return False

        total_entries = len(manifest_entries)
        ok_count = 0
        skip_count = 0
        error_count = 0
        # One record per entry, feeding the end-of-run recap.
        entry_reports: List[Dict] = []

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Executing manifest: {total_entries} entry(ies)[/bold cyan]")
            console.print(f"[dim]Script: {_rich_safe(script)} | Workers: {workers}[/dim]")
            if dry_run:
                console.print("[yellow]DRY RUN MODE[/yellow]")
            console.print()
        else:
            print(f"\nExecuting manifest: {total_entries} entry(ies)")
            print(f"Script: {script} | Workers: {workers}")
            if dry_run:
                print("DRY RUN MODE")
            print()

        # resolved_entries, not manifest_entries: the modes were resolved once
        # above (detect_mode_for_entry preserves an explicit Mode cell and only
        # detects for legacy manifests), so the guards and the run can never
        # disagree about what mode an entry is.
        for i, (source, dest_path, detected_mode) in enumerate(resolved_entries, 1):
            if RICH_AVAILABLE and console:
                console.print(f"[{i}/{total_entries}] [bold]{detected_mode}[/bold] | {_rich_safe(self._truncate_path(source, 40))} → {_rich_safe(self._truncate_path(dest_path, 40))}")
            else:
                print(f"[{i}/{total_entries}] Mode {detected_mode} | {source} → {dest_path}")

            # Build command for this entry (the builder appends --dry-run when
            # workflow['dry_run'] is set, so the child prints the REAL output
            # paths instead of a vague "would process"). The row's own
            # manifest option columns ride in wf_row/adv_row (resolved_entries
            # is 1:1 with manifest_entries, in the same order, so i-1 indexes
            # the row's options).
            wf_row, adv_row = _row_effs[i - 1]
            cmd = self._build_manifest_entry_cmd(
                script=script,
                source=source,
                dest_path=dest_path,
                mode=detected_mode,
                origin=origin,
                dest=dest,
                workers=workers,
                workflow=wf_row,
                advanced=adv_row
            )

            if cmd is None:
                error_count += 1
                entry_reports.append({
                    "index": i, "mode": detected_mode, "source": source,
                    "state": "not started", "summary": None,
                })
                continue

            # Execute
            try:
                rc = self._run_subprocess(cmd)
            except KeyboardInterrupt:
                # Ctrl+C: _stream_child has already killed the child. Record
                # the entry, mark the rest not started and fall through to the
                # summary — a manifest runs for hours, and the accounting of
                # what DID complete must survive the interruption. main() then
                # reports the cancellation and exits 130.
                error_count += 1
                entry_reports.append({
                    "index": i, "mode": detected_mode, "source": source,
                    "state": "cancelled", "summary": self._last_child_summary,
                    "deletes": "--delete-source" in [str(c) for c in cmd],
                })
                for j, (src_rest, _dst_rest, m_rest) in enumerate(
                        resolved_entries[i:], i + 1):
                    entry_reports.append({
                        "index": j, "mode": m_rest, "source": src_rest,
                        "state": "not started", "summary": None,
                    })
                self._render_manifest_summary(entry_reports, ok_count,
                                              skip_count, error_count)
                raise
            aborted = False
            usage_err = None
            killed = False
            interrupted = False
            if rc == 0:
                ok_count += 1
                state = "ok"
            elif rc == 3:
                # User declined the child's confirmation prompt
                skip_count += 1
                state = "cancelled"
                if RICH_AVAILABLE and console:
                    console.print("  [yellow]Cancelled by user[/yellow]")
                else:
                    print("  Cancelled by user")
            elif rc == 2:
                # The child gave up on a condition that spans the whole run
                # (a full output volume, a safety abort) — every remaining
                # entry would hit the same wall. Stopping here is the point
                # of exit 2: the children already refuse to grind on, and the
                # manifest loop relaunching them entry after entry put that
                # behavior straight back.
                #
                # argparse also exits 2, and that case is NOT the same: the
                # child never started, so no file was touched by this entry —
                # and every remaining entry carries the same flags, so it would
                # die identically. Stopping is still right; calling it a safety
                # abort is not (see _CHILD_USAGE_ERROR_RE).
                #
                # Contract update: rc == -1 (below) is the SAME fatal class.
                # It means the wrapper never got a real exit code from the
                # child — killed by the idle timeout mid-work, never launched,
                # or deadlocked past the post-run wait — and the remaining
                # entries are not relaunched either.
                error_count += 1
                usage_err = self._last_child_usage_error
                state = "rejected" if usage_err else "aborted"
                aborted = True
            elif rc == -1:
                # -1 is not "some files failed": _stream_child returns it when
                # the child was KILLED by the idle timeout in the middle of its
                # work, never launched at all, or hung past the post-run wait.
                # A child killed mid-encode / mid-delete-gate / mid-move leaves
                # state nobody can reason about, and a launch failure would
                # repeat identically for every remaining entry — relaunching
                # the rest is "a few files failed" accounting for what is a
                # stop-everything condition. Treated exactly like exit 2.
                error_count += 1
                state = "killed"
                killed = True
                aborted = True
            elif rc == 130:
                # Ctrl+C reached the CHILD (128+SIGINT — the recompressor's
                # documented interrupt path, which prints its own summary
                # before exiting). The operator asked to STOP: relaunching
                # the remaining entries is the opposite of that, so this is
                # the same stop-everything class as exit 2 and -1. (When the
                # wrapper itself takes the interrupt, _stream_child raises
                # KeyboardInterrupt and the except above runs instead.)
                error_count += 1
                state = "interrupted"
                interrupted = True
                aborted = True
            else:
                error_count += 1
                state = "failed"

            entry_reports.append({
                "index": i, "mode": detected_mode, "source": source,
                "state": state, "summary": self._last_child_summary,
                "deletes": "--delete-source" in [str(c) for c in cmd],
            })

            if aborted:
                not_started = total_entries - i
                if usage_err:
                    msg = (f"Entry REJECTED by {Path(script).name}: {usage_err}. "
                           f"This entry never started, and every remaining entry "
                           f"carries the same flags, so the manifest stops here. "
                           f"This is a WRAPPER bug — it built a command the script "
                           f"does not accept — not a problem with your files.")
                elif killed:
                    # Not the exit-2 wording: a KILLED child may have died in
                    # the middle of a file (or a delete), so even "nothing was
                    # deleted after the abort" cannot be promised. The manifest
                    # stops; the summary above shows which entries completed.
                    msg = ("Entry killed (exit -1): the child was terminated — "
                           "no output for too long, a launch failure, or a hang "
                           "past the wait timeout — possibly in the middle of a "
                           "file. The rest of the manifest will NOT be "
                           "relaunched: a launch failure would repeat for every "
                           "entry, and a child killed mid-run must not be "
                           "followed by more deleting. Check the child log, fix "
                           "the cause and re-run — sync mode resumes where this "
                           "stopped.")
                elif interrupted:
                    # Ctrl+C was caught by the CHILD (exit 130), not by this
                    # wrapper. Saying "exit 2" here would read as a safety
                    # abort that never happened.
                    msg = ("Entry interrupted (exit 130, Ctrl+C inside the "
                           "child): the manifest stops here instead of "
                           "launching the remaining entries. Re-run it — "
                           "sync mode resumes where this stopped.")
                else:
                    # "Nothing was deleted" was a promise the wrapper could not
                    # keep: exit 2 is also the disk-full abort, which fires part
                    # way through a run, and entries that already finished did
                    # their own deleting. Only what came AFTER the abort is
                    # certain.
                    msg = ("Entry aborted (exit 2): stopping the manifest. "
                           "Nothing was deleted after the abort — but sources "
                           "deleted by the groups and entries that already "
                           "completed stay deleted. Fix the cause and re-run — "
                           "sync mode resumes where this stopped.")
                if not_started:
                    msg += f" {not_started} remaining entry(ies) were NOT started."
                self._print_error(msg)
                for j, (src_rest, _dst_rest, m_rest) in enumerate(
                        resolved_entries[i:], i + 1):
                    entry_reports.append({
                        # Already resolved above, like the loop's own entries —
                        # the recap formats this as an int.
                        "index": j,
                        "mode": m_rest,
                        "source": src_rest,
                        "state": "not started", "summary": None,
                    })
                break

        # Summary: per-entry recap + grand total + every failed file. A manifest
        # runs for hours and the user walks away — coming back to a single
        # "3 OK" line meant opening N child logs to learn whether anything
        # broke in the middle.
        self._render_manifest_summary(entry_reports, ok_count, skip_count, error_count)

        return error_count == 0

    def _render_manifest_summary(self, entry_reports: List[Dict], ok_count: int,
                                 skip_count: int, error_count: int) -> None:
        """Print the end-of-manifest block and write the combined wrapper log."""
        totals = {"ok": 0, "overwritten": 0, "skipped": 0, "unreadable": 0, "errors": 0}
        extras_total: Dict[str, int] = {}
        failures: List = []      # (entry_index, file, reason)
        unreadable: List = []    # same shape; kept apart — broken input, not a run failure
        child_logs: List[str] = []
        truncated = False
        any_dry_run = False

        for rep in entry_reports:
            s = rep["summary"]
            if not s:
                continue
            for k in totals:
                totals[k] += s[k]
            for label, value in s["extras"].items():
                extras_total[label] = extras_total.get(label, 0) + value
            for f, reason in s["failures"]:
                failures.append((rep["index"], f, reason))
            for f, reason in s["unreadable_files"]:
                unreadable.append((rep["index"], f, reason))
            truncated = truncated or s["failures_truncated"]
            any_dry_run = any_dry_run or s["dry_run"]
            if s["log"]:
                child_logs.append(s["log"])

        lines = self._build_manifest_summary_lines(
            entry_reports, totals, extras_total, failures, unreadable, child_logs,
            ok_count, skip_count, error_count, truncated, any_dry_run,
        )

        if RICH_AVAILABLE and console:
            from rich.markup import escape as _escape
            console.print()
            for text, style in lines:
                console.print(f"[{style}]{_escape(text)}[/{style}]" if style else _escape(text))
        else:
            print()
            for text, _style in lines:
                print(text)

        # Written after the block is built (it contains the block) and printed
        # on its own line: a full Windows path plus a label wraps mid-path.
        log_path = self._write_manifest_log(lines, entry_reports, failures,
                                            unreadable, child_logs)
        if log_path:
            if RICH_AVAILABLE and console:
                from rich.markup import escape as _escape
                console.print("[dim]Combined log:[/dim]")
                console.print(f"[dim]  {_escape(str(log_path))}[/dim]", soft_wrap=True)
            else:
                print("Combined log:")
                print(f"  {log_path}")

    # How many failed files to show on screen. The rest stay in the combined
    # log — the point of the screen list is "something broke, here's where",
    # not a full incident report.
    SUMMARY_SCREEN_FAILURES = 15

    def _build_manifest_summary_lines(self, entry_reports: List[Dict], totals: Dict,
                                      extras_total: Dict[str, int], failures: List,
                                      unreadable: List, child_logs: List[str],
                                      ok_count: int, skip_count: int, error_count: int,
                                      truncated: bool, any_dry_run: bool) -> List:
        """Build the block as (text, rich_style) pairs — shared by screen and log.

        Laid out to fit an 80-column terminal: a wrapped table is harder to
        read at a glance than the scrolled-away lines this replaces.
        """
        width = 75
        rule = "-" * width
        lines: List = [("=" * width, "dim")]

        # Counts entries, not files — the table below counts files, and mixing
        # the two in one line read as a contradiction ("3 OK" over "5762 OK").
        # The header's parts must SUM to the total: the not-started entries
        # appended after an abort/interrupt sit in no ok/error/cancel bucket
        # (only the entry that aborted does), so they get their own bucket —
        # a 5-entry run aborted at #2 used to read "5 entries - 1 ok, 1 with
        # failures, 0 cancelled" and leave three entries unaccounted (W2-4).
        _not_started = (len(entry_reports)
                        - (ok_count + skip_count + error_count))
        head = (f"Manifest complete: {len(entry_reports)} entries - "
                f"{ok_count} ok, {error_count} with failures, {skip_count} cancelled")
        if _not_started > 0:
            head += f", {_not_started} not started"
        if any_dry_run:
            head = "[DRY RUN] " + head
        lines.append((head, "bold red" if error_count else "bold"))
        lines.append((rule, "dim"))
        lines.append((f"  {'#':<3}{'mode':<5}{'folder':<28}{'OK':>7}{'ovw':>7}"
                      f"{'skip':>7}{'corrupt':>8}{'err':>7}", "dim"))

        # Per-entry recap. Manifest entries are folders the user chose by hand,
        # so there are few of them even when each holds thousands of files —
        # no need to collapse this list.
        for rep in entry_reports:
            label = (f"  {rep['index']:<3}{rep['mode']:<5}"
                     f"{self._truncate_path(rep['source'], 28):<28}")
            s = rep["summary"]
            if s:
                text = (f"{label}{s['ok']:>7}{s['overwritten']:>7}"
                        f"{s['skipped']:>7}{s['unreadable']:>8}{s['errors']:>7}")
                style = "red" if s["errors"] else ("yellow" if s["unreadable"] else "")
            else:
                # No summary line: the child crashed, was killed, or never
                # launched. Saying so beats showing zeros as if nothing failed.
                text = f"{label}   (no summary - {rep['state']})"
                style = "yellow" if rep["state"] == "cancelled" else "red"
            lines.append((text, style))

        lines.append((rule, "dim"))
        lines.append((f"  {'TOTAL files':<36}{totals['ok']:>7}{totals['overwritten']:>7}"
                      f"{totals['skipped']:>7}{totals['unreadable']:>8}{totals['errors']:>7}",
                      "bold red" if totals["errors"] else "bold"))

        # Deletions get their own line, above the rest and not dimmed: they are
        # the only irreversible thing in the block, and burying them among
        # "Thumbnails excluded" made a run that removed 50 000 masters look
        # exactly like one that removed none.
        _del = extras_total.pop("Sources deleted", 0)
        _del_arch = extras_total.pop("Sources deleted (already archived)", 0)
        _kept = extras_total.pop("Sources KEPT by a delete gate", 0)
        _refused = extras_total.pop("Refused (output belongs to another source)", 0)
        # W8 (2026-10-08 audit): the totals come from the ##JXLSUM## lines, and
        # a child killed (idle timeout), crashed or interrupted emits none — so
        # whatever its delete gate had already removed was missing from
        # "SOURCES DELETED" with nothing saying the count was incomplete.
        _uncounted = [r for r in entry_reports
                      if r.get("deletes") and not r.get("summary")]
        if _del or _kept or _refused or _uncounted:
            lines.append((rule, "dim"))
            _txt = f"  SOURCES DELETED: {_del}"
            if _del_arch:
                _txt += f"  ({_del_arch} already archived)"
            lines.append((_txt, "bold red" if _del else "bold"))
            if _kept:
                lines.append((f"  Sources KEPT by a delete gate: {_kept} "
                              f"(see the child logs for why)", "yellow"))
            if _refused:
                lines.append((f"  Refused, output belongs to another source: {_refused}",
                              "yellow"))
            if _uncounted:
                _n = len(_uncounted)
                lines.append((f"  NOT COUNTED: {_n} entr{'y' if _n == 1 else 'ies'} with "
                              f"--delete-source ended without a summary "
                              f"({', '.join(sorted({str(r.get('state')) for r in _uncounted}))})"
                              f" — whatever {'it' if _n == 1 else 'they'} deleted is not in "
                              f"the number above; see the child log(s)", "bold red"))

        if extras_total:
            lines.append((rule, "dim"))
            lines.append(("  " + "  |  ".join(f"{k}: {v}" for k, v in sorted(extras_total.items())), "dim"))

        def _file_section(title: str, items: List, style: str, note: str = "") -> None:
            if not items:
                return
            lines.append((rule, "dim"))
            lines.append((f"  {title} ({len(items)}):", f"bold {style}"))
            if note:
                lines.append((f"  {note}", style))
            for entry_idx, path, reason in items[:self.SUMMARY_SCREEN_FAILURES]:
                lines.append((f"    [{entry_idx}] {path}", style))
                if reason:
                    lines.append((f"        -> {reason}", style))
            hidden = len(items) - self.SUMMARY_SCREEN_FAILURES
            if hidden > 0:
                lines.append((f"    ... and {hidden} more (full list in the combined log)", style))

        _file_section("FAILURES", failures, "red")
        if truncated:
            lines.append(("    Note: a child capped its failure list; see its own log for the rest.", "yellow"))
        # Kept out of FAILURES on purpose: these files were never converted, but
        # the run did not fail — the input is broken, not the process.
        _file_section("CORRUPT / UNREADABLE", unreadable, "yellow",
                      note="These were NOT converted. The source files are damaged.")

        if child_logs:
            lines.append((rule, "dim"))
            lines.append((f"  Child logs ({len(child_logs)}):", "dim"))
            for p in child_logs:
                lines.append((f"    {p}", "dim"))

        lines.append(("=" * width, "dim"))
        return lines

    def _write_manifest_log(self, lines: List, entry_reports: List[Dict],
                            failures: List, unreadable: List,
                            child_logs: List[str]) -> Optional[Path]:
        """Write the combined run log. Returns its path, or None if it failed.

        The screen block truncates the failure list; this file never does — it
        is what the user opens hours later, after the scrollback is gone.
        """
        from datetime import datetime
        try:
            WRAPPER_LOG_DIR.mkdir(parents=True, exist_ok=True)
            path = WRAPPER_LOG_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"jxl_photo manifest run — {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
                for text, _style in lines:
                    fh.write(text + "\n")
                # The table truncates folders to fit the terminal; the log is
                # the durable copy, so it also carries the untruncated paths.
                fh.write("\nENTRIES (full paths)\n")
                for rep in entry_reports:
                    fh.write(f"  [{rep['index']}] mode {rep['mode']} | {rep['state']} | {rep['source']}\n")
                # The screen list is capped; this file is the uncapped copy.
                for title, items in (("FULL FAILURE LIST", failures),
                                     ("FULL CORRUPT / UNREADABLE LIST", unreadable)):
                    if len(items) > self.SUMMARY_SCREEN_FAILURES:
                        fh.write(f"\n{title}\n")
                        for entry_idx, p, reason in items:
                            fh.write(f"  [{entry_idx}] {p}\n      -> {reason}\n")
            return path
        except OSError as e:
            # A log we could not write must not fail a run whose real work is
            # already done — the block was printed to screen either way.
            self._print_error(f"Could not write combined log: {e}")
            return None

    @staticmethod
    def _entry_marker_dir(source: str, export_marker: str) -> Optional[str]:
        """Directory that modes 6/7 anchor `source` on, or None if the marker is
        not part of the Source path.

        Modes 6/7 write into <marker>/<constant>/, which is a SIBLING of the
        source folder rather than a child of it — so two entries anchored on the
        SAME marker directory share one output folder even when their Sources do
        not overlap at all (_EXPORT/TIFF16 and _EXPORT/AdobeRGB).

        Every part of a manifest Source is a directory, so (unlike the children,
        which anchor on a FILE path) there is no filename to exclude here.
        """
        parts = Path(source).parts
        marker_lower = (export_marker or ToolConfig.export_marker).lower()
        # The DEEPEST match, like the children's anchor for a run pointed at
        # this Source (_marker_anchor_index, E-H): an outer folder that also
        # matches ('Lightroom_Export') is not where the outputs go.
        idx = next((i for i in range(len(parts) - 1, -1, -1)
                    if _marker_matches(parts[i].lower(), marker_lower)), None)
        if idx is None:
            return None
        return str(Path(*parts[:idx + 1]))

    def _manifest_needs_collision_scan(self, manifest_entries: List,
                                       export_marker: str,
                                       row_markers: Optional[List[str]] = None) -> bool:
        """Can any two entries write to the same output folder?

        The scan below is expensive (a recursive walk per Source), so it is worth
        skipping when the answer is provably no. It is NOT enough to look at the
        mode alone — the earlier "modes 0/1/3/6/7/8 write only inside their own
        Source tree" rule was wrong twice over:

          * mode 0 is the one "per-source" mode that HONORS the Destination
            column, so it is only in-place while Destination == Source;
          * modes 6/7 anchor on the export marker and write to a SIBLING of the
            Source, shared by every entry under the same marker.

        Modes 1/3/8 do derive their output from the Source itself (subfolder or
        in-place). Modes 2/4/5 always can collide.

        6/7 whose marker sits BELOW the Source belong to the first family too:
        every marker folder they anchor on is inside that Source, so every
        output is as well. That case used to force a scan unconditionally, and
        it is the common one — a manifest of `G:\\2024`, `G:\\2025`, `G:\\2026`
        in mode 6 walked all three libraries before converting a single file,
        to look for a collision that disjoint trees cannot produce.

        What makes the "writes inside its own Source" family safe is that their
        Sources do not overlap, so that is checked here rather than assumed:
        overlaps are only WARNED about upstream (attended runs may continue),
        which is weaker than this function had been relying on.

        `row_markers` (optional): the export marker each entry actually runs
        with (its ExportMarker column, or the run's), aligned with
        manifest_entries. When omitted, `export_marker` is used for every row.
        Per-row markers can NEST — one row anchored on `_EXPORT`, another on
        `_EXPORT/SITE` — which the exact-marker-key comparison is blind to, so
        nested marker dirs force the scan as well.
        """
        marker_dirs: Dict[str, str] = {}
        # Sources whose outputs stay inside their own tree. Disjoint ones can
        # never share an output folder; overlapping ones can.
        within_source: List[str] = []
        for i, (source, dest_path, mode) in enumerate(manifest_entries):
            # Legacy manifest (no Mode cell): the mode is detected per folder
            # downstream, so nothing can be ruled out here.
            if mode is None:
                return True
            if mode in (2, 4, 5):
                return True
            if mode == 0:
                same = bool(dest_path) and (
                    os.path.normcase(str(Path(dest_path)))
                    == os.path.normcase(str(Path(source))))
                if not same:
                    return True
                within_source.append(source)
            elif mode in (6, 7):
                row_marker = (row_markers[i] if row_markers is not None
                              and i < len(row_markers) and row_markers[i]
                              else export_marker)
                marker = self._entry_marker_dir(source, row_marker)
                if marker is None:
                    # The marker is below the Source: outputs land under marker
                    # folders inside this tree.
                    within_source.append(source)
                    continue
                # The marker is an ANCESTOR of the Source, so the output folder
                # is a sibling of it — shared by every entry under that marker.
                key = os.path.normcase(marker)
                if key in marker_dirs:
                    return True
                marker_dirs[key] = source
            else:
                # 1, 3, 8: a subfolder of the Source, or in place.
                within_source.append(source)

        norm = [os.path.normcase(os.path.abspath(s)) for s in within_source]
        for i, a in enumerate(norm):
            for b in norm[i + 1:]:
                if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
                    return True

        # With ONE shared marker, two entries in the same path always resolve
        # to the same marker dir (the outermost), so the exact-key check inside
        # the loop already catches them. With per-row markers, one row's marker
        # dir can sit INSIDE another's — `_EXPORT` and `_EXPORT/SITE` are
        # different keys, so that comparison never sees it, yet both children
        # write their (sibling) output folders into the shared tree. Treat any
        # nesting between marker dirs as unsafe and force the scan.
        marker_norm = [os.path.normcase(os.path.abspath(m))
                       for m in marker_dirs]
        for i, a in enumerate(marker_norm):
            for b in marker_norm[i + 1:]:
                if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
                    return True

        # Cross-family check — the bug this guards: the two families were only
        # ever compared WITHIN themselves, so a marker-anchored entry and a
        # within_source entry writing the SAME folder went unseen. Example: a
        # mode-6 entry anchored on G:\_EXPORT (its outputs land in
        # G:\_EXPORT\16B_JXL) next to a mode-0 in-place entry whose Source IS
        # G:\_EXPORT\16B_JXL — disjoint Sources in both families, one shared
        # output folder written by two child processes.
        #
        # The marker family's output folder is a constant subfolder of the
        # marker dir (EXPORT_JXL_FOLDER "16B_JXL" for the encoder,
        # EXPORT_TIFF_FOLDER for the decoder, per-direction folders for the
        # transcoder). This function does not know the run's direction, so it
        # cannot cheaply name that subfolder — and must fail SAFE: because the
        # output folder sits strictly INSIDE the marker dir, any containment
        # between a within_source Source and a marker dir (either way) can put
        # outputs on it, and that is enough to force the scan. The cost is a
        # false-positive scan for harmless mixes like mode 0 on
        # <marker>\TIFF16 next to mode 7 on <marker>\16bit — rare, and cheaper
        # than the collision it replaces.
        if marker_dirs and norm:
            for key in marker_dirs:
                m = os.path.normcase(os.path.abspath(key))
                for s in norm:
                    if s == m or s.startswith(m + os.sep) or m.startswith(s + os.sep):
                        return True
        return False

    def _manifest_output_collisions(self, manifest_entries: List, origin_exts: set,
                                    origin: str = None, dest: str = None,
                                    export_marker: str = None,
                                    export_subfolder: str = None,
                                    export_jxl_folder: str = None,
                                    rename_from: str = '',
                                    rename_to: str = '',
                                    row_renames: Optional[List[Tuple[str, str]]] = None,
                                    row_export: Optional[List[Tuple]] = None,
                                    cross_sink: Optional[Dict[int, Set[str]]] = None,
                                    row_excludes: Optional[List[Optional[str]]] = None,
                                    conversion_type: str = None,
                                    page_options: Optional[Dict] = None) -> List:
        """Find files from DIFFERENT manifest entries that would be written to
        the same output file.

        Each manifest entry runs as a SEPARATE child process, so the children's
        own duplicate-output guard (_abort_on_duplicate_outputs) can never see a
        conflict that spans two entries. Mode 5 is the case that bites: every
        source folder collapses into the same sibling folder, so
        sub1/foto.tif and sub2/foto.tif both target <root>/JXL_16bits/foto.jxl.
        A direct run aborts on this; a manifest run silently converts one and
        skips (or overwrites) the other while reporting 0 errors.

        Outputs are resolved with the CHILD SCRIPT'S OWN resolver (imported
        lazily, only when origin/dest are known). Two blind spots of the old
        flat-Destination scan motivated this:

          * Mode 2 is recursive-FLAT: every file under the source lands in the
            Destination, but the old scan only looked at direct children, so
            A/deep/foto.tif + B/foto.tif -> same foto.jxl went unseen.
          * Modes 1/3/4/5/6/7 IGNORE the Destination column and compute their
            output folders from script constants — and an empty Destination
            cell falls back to Source at load time, which bucketed every entry
            separately and detected nothing (the hand-written mode-5 manifest
            this guard was built for).

        Using the real resolver also honors user-edited folder constants and
        returns None for files the child would skip (e.g. outside the export
        marker in modes 6/7), keeping the guard free of collisions the child
        would never produce.

        `cross_sink` (optional): when a dict is passed, it is filled with each
        entry index -> the set of output folders that entry resolved to. The
        cross-entry guard (_manifest_output_source_collisions) reuses exactly
        this notion of "output tree" — it cannot be computed from the Destination
        cells alone, because modes 1/3/4/5/6/7 derive their outputs from
        script constants.

        `row_renames` (optional): one (rename_from, rename_to) per entry,
        aligned with manifest_entries — a manifest RenameFrom/RenameTo column
        renames ONE row's outputs, so the global rename pair is only the
        fallback for rows without their own.

        `row_export` (optional): one (marker, subfolder, jxl_folder) per entry,
        aligned with manifest_entries and ALREADY resolved with the global
        fallback. The child's finder/resolver read those as module globals, so
        the `_with_child_marker` context now enters PER ENTRY: an entry with
        its own Export* columns must resolve with those, not the run's. When
        None, the (export_marker, export_subfolder, export_jxl_folder)
        arguments are the fallback for every entry.

        `row_excludes` (optional): one raw exclude_folders value (';' string
        or None) per entry, aligned with manifest_entries — the manifest's
        ExcludeFolders column, ALREADY effective per row. The children's
        finders never run here (this walk goes through its own rglob +
        resolver), so the exclusion must be applied in the walk's resolver
        branch or a flat-output row would abort on PHANTOM collisions: the
        excluded file would never be processed at all. When None, no entry
        has an exclusion (the only real caller always passes the list).
        The filter is only applied for the tiff<->jxl directions — the only
        ones whose child ever RECEIVES --exclude-folders (the cmd builder's
        emission gate). Filtering anywhere else would shrink the walk below
        what the child processes and hide REAL collisions.

        `conversion_type` (optional): the workflow's conversion type. The
        transcoder recovers jbrd files with their ORIGINAL name — it ignores
        --rename-from on that path (and on jxl_to_jpeg_lossless entirely) —
        so the scan must not model the rename for those files, in either
        direction: applying it misses real collisions (two rows, renames
        x->a and x->b, child writes x.jpg twice) and inventing it refuses
        legitimate runs. Files are probed once, with has_jbrd_box cached.

        `page_options` (optional): the run's advanced options, read for
        'multipage_mode'/'thumbnail_mode'/'thumbnail_suffix' (the encoder's
        own settings when absent). A split TIFF writes photo_page1.jxl,
        photo_page2_thumbnail.jxl... next to photo.jxl, and those names can
        land on ANOTHER file's output — photo_page1.tif in a second entry
        (W9 of the 2026-10-08 audit). Only page-shaped names next to their
        base name are probed, so the scan reads a TIFF header only for
        those pairs.

        Returns a list of (file_a, file_b, dest_folder) tuples.
        """

        resolver = None
        _skip_check = None
        _child = None
        if origin and dest:
            try:
                if origin == 'tiff' and dest == 'jxl':
                    import jxl_tiff_encoder as _child
                    out_ext, conv_folder = '.jxl', _child.CONVERTED_JXL_FOLDER
                elif origin == 'jxl' and dest == 'tiff':
                    import jxl_tiff_decoder as _child
                    out_ext, conv_folder = '.tif', _child.CONVERTED_TIFF_FOLDER
                elif origin == 'jxl' and dest == 'jxl':
                    import jxl_recompressor as _child
                    out_ext, conv_folder = '.jxl', _child.CONVERTED_JXL_FOLDER
                elif origin in ('jpeg', 'jxl'):
                    import jxl_jpeg_transcoder as _child
                    out_ext = '.jpg' if origin == 'jxl' else '.jxl'
                    conv_folder = (_child.RECOVERED_JPEG_FOLDER if origin == 'jxl'
                                   else _child.CONVERTED_JXL_FOLDER)
            except ImportError:
                _child = None
            if _child is not None:
                def _apply_rename_name(name: str, rf: str, rt: str) -> str:
                    """Literal first-occurrence, case-sensitive stem
                    replacement — the semantics the recompressor's
                    _apply_rename and the transcoder's resolve_output_convert
                    share (a token that differs only by case does NOT rename)."""
                    if rf:
                        stem, ext = os.path.splitext(name)
                        if rf in stem:
                            return stem.replace(rf, rt, 1) + ext
                    return name

                def _rename_out(p: Optional[Path], rf: str, rt: str) -> Optional[Path]:
                    """--rename-from changes the output NAME at planning time
                    (all modes, 0/1/2 included): mirror it, so a collision the
                    rename creates is caught here too (folders are unchanged).
                    The transcoder applies the rename on its decode/convert
                    paths only, but the manifest option columns are only ever
                    offered for the directions that honour them."""
                    if p is not None and rf:
                        return p.with_name(_apply_rename_name(p.name, rf, rt))
                    return p

                # Per-file jbrd probe, cached: the scan calls it twice per
                # file (resolver + stem key), and only when a rename is in
                # play at all.
                _jbrd_cache: Dict[str, bool] = {}

                def _jbrd_keeps_name(f: Path) -> bool:
                    """True when THIS file's output keeps the original stem
                    even with --rename-from in play: the transcoder recovers
                    jbrd files byte-for-byte under the ORIGINAL JPEG name (the
                    child logs "rename ignored"), and jxl_to_jpeg_lossless
                    never renames at all. Modelling the rename there
                    desynchronizes the guard from the run both ways (a missed
                    real collision AND a refused legitimate one)."""
                    if (_child.__name__ != 'jxl_jpeg_transcoder'
                            or origin != 'jxl' or dest not in ('jpeg', 'jpg')):
                        return False
                    if conversion_type == 'jxl_to_jpeg_lossless':
                        return True
                    if conversion_type != 'jxl_to_jpeg_auto':
                        return False
                    key = os.path.normcase(str(f))
                    hit = _jbrd_cache.get(key)
                    if hit is None:
                        try:
                            hit = bool(_child.has_jbrd_box(f))
                        except OSError:
                            hit = False
                        _jbrd_cache[key] = hit
                    return hit

                def resolver(f: Path, mode: int, src_root: Path, dest_cell: str,
                             rf: str, rt: str) -> Optional[Path]:
                    # Mode 0 HONORS the Destination column: the cmd builder
                    # always passes it as the child's output positional, and
                    # every child resolves `output_root = args.output or
                    # args.input`. Resolving mode 0 in-place made the scan that
                    # #234 deliberately re-opened for it find nothing at all —
                    # two entries sharing one Destination collapsed onto a
                    # single output in silence. Mode 0 is FLAT, so f.parent ==
                    # src_root and a Destination equal to the Source resolves
                    # identically: one branch covers both.
                    # The rename THIS file actually gets: empty when the child
                    # will ignore it (jbrd recovery / the lossless decode).
                    erf, ert = ((rf, rt) if rf and not _jbrd_keeps_name(f)
                                else ('', ''))
                    if mode in (0, 2):
                        return _rename_out(Path(dest_cell) / (f.stem + out_ext), erf, ert)
                    if mode == 8:
                        return _rename_out(f.parent / (f.stem + out_ext), erf, ert)
                    if mode == 1:
                        return _rename_out(src_root / conv_folder / (f.stem + out_ext), erf, ert)
                    if _child.__name__ == 'jxl_jpeg_transcoder':
                        # The transcoder has TWO resolvers and this used
                        # resolve_output_transcode for every direction — the
                        # lossless JPEG<->JXL one. The lossy convert paths
                        # (PNG, and JXL->JPEG without jbrd) land in different
                        # folders, so the guard was comparing paths the run
                        # would never write.
                        #
                        # It has not produced a wrong VERDICT, because both
                        # entries were resolved by the same wrong function and
                        # the difference is a constant folder name — a
                        # collision under one mapping is a collision under the
                        # other. Fixing it anyway: the next person to add a
                        # per-direction rule to either resolver would inherit a
                        # guard that silently disagrees with the run.
                        _dec = origin == 'jxl'
                        if dest in ('png', 'jpeg', 'jpg') and _dec:
                            return _child.resolve_output_convert(
                                f, mode, _child.CONVERT_OUTPUT_FOLDER, "",
                                (dest if dest != 'jpg' else 'jpeg'),
                                rename_from=erf, rename_to=ert,
                                output_root=src_root, decode=True)
                        return _child.resolve_output_transcode(f, mode, src_root, _dec)
                    return _rename_out(_child.resolve_output(f, mode, src_root), erf, ert)

                def _stem_key(f: Path, rf: str, rt: str) -> str:
                    """The output stem this source will land under — renamed when
                    the row's --rename-from applies, exactly like the child
                    plans it (a jbrd recovery keeps the ORIGINAL name)."""
                    if rf and not _jbrd_keeps_name(f):
                        name = _apply_rename_name(f.name, rf, rt)
                        return os.path.normcase(os.path.splitext(name)[0])
                    return os.path.normcase(f.stem)

                # Which files would the child itself skip on its scan? Mirror
                # EXACTLY that — a wrong skip set works both ways: it reports
                # collisions the child would never produce (false positive ->
                # legit manifest refused) and misses files the child WOULD
                # process (false negative -> the blind spot is back):
                #
                #   * encoder (tiff->jxl): skips decoder-output folders below
                #     the export marker, and ONLY in modes 6/7 — its other
                #     finders (flat/recursive) are unfiltered;
                #   * transcoder ENCODE (jpeg->jxl): skips its own output
                #     folders via _is_tool_output_path, and only on recursive
                #     scans (modes 2-8) — the flat finders are unfiltered;
                #   * decoder (jxl->tiff): in modes 6/7 only, the OTHER
                #     tools' JXL output folders below the export marker
                #     (_skip_other_tool_output, X-B) — every other finder is
                #     unfiltered (round-trip sources must be found);
                #   * transcoder DECODE (jxl->jpeg): deliberately UNFILTERED
                #     (it is how a JPEG archive comes back) — no skip at all.
                def _other_tool_skip(f: Path, root: Path, mode: int) -> bool:
                    """The decoder's/recompressor's mode 6/7 filter (X-B),
                    mirrored: a folder below the marker that is another
                    tool's JXL output folder."""
                    if mode not in (6, 7) or not hasattr(_child, '_skip_other_tool_output'):
                        return False
                    parts = list(f.parts[:-1])
                    marker_lower = _child.EXPORT_MARKER.lower()
                    idx = _child._marker_anchor_index(parts, root, marker_lower)
                    if idx is None:
                        return False
                    below = [p.lower() for p in parts[idx + 1:]]
                    # Mode 6's finder passes honor_requested_subfolder=False.
                    return _child._skip_other_tool_output(
                        below, honor_requested_subfolder=(mode != 6))

                if _child.__name__ == 'jxl_tiff_encoder':
                    def _skip_check(f: Path, root: Path, mode: int) -> bool:
                        if mode not in (6, 7):
                            return False
                        parts = list(f.parts[:-1])
                        marker_lower = _child.EXPORT_MARKER.lower()
                        # The children's anchor rule (E-H): the first marker
                        # at or below the row's root, else the closest above.
                        idx = _child._marker_anchor_index(parts, root, marker_lower)
                        if idx is None:
                            return False
                        below = [p.lower() for p in parts[idx + 1:]]
                        # Mode 6's real finder calls this with
                        # honor_requested_subfolder=False (jxl_tiff_encoder.py);
                        # mirroring the default True here could only ever
                        # produce a false-positive collision — keep them equal.
                        return _child._skip_decoder_output(
                            below, honor_requested_subfolder=(mode != 6))
                elif _child.__name__ == 'jxl_jpeg_transcoder' and origin == 'jpeg':
                    def _skip_check(f: Path, root: Path, mode: int) -> bool:
                        if mode < 2:
                            return False
                        return _child._is_tool_output_path(f, root)
                elif _child.__name__ == 'jxl_recompressor':
                    def _skip_check(f: Path, root: Path, mode: int) -> bool:
                        # Its recursive finders (modes 2-8) skip this tool's OWN
                        # output folder names BELOW the input root (pointing a
                        # run AT such a folder is legitimate); the flat finders
                        # are unfiltered. Mode 4's FALLBACK output folder
                        # ("<name>_JXL_small", created when the JXL token is
                        # missing from the folder name) is its output too:
                        # recognized as a _jxl_small SUFFIX, the same semantic
                        # change _is_own_output_path received in
                        # jxl_recompressor.py. Checked here as well (not only
                        # via the child) so the mirror keeps guarding even if
                        # the child copy it imports ever drifts back — and the
                        # "only BELOW the root" rule is untouched: `below` is
                        # still computed relative to the root, exactly like the
                        # child's own finders compute it.
                        if mode < 2:
                            return False
                        try:
                            below = [p.lower() for p in f.relative_to(root).parts[:-1]]
                        except ValueError:
                            below = [p.lower() for p in f.parts[:-1]]
                        return (_child._is_own_output_path(below)
                                or any(p.endswith("_jxl_small") for p in below)
                                or _other_tool_skip(f, root, mode))
                elif _child.__name__ == 'jxl_tiff_decoder':
                    def _skip_check(f: Path, root: Path, mode: int) -> bool:
                        return _other_tool_skip(f, root, mode)

        by_dest: Dict[str, Dict[str, Path]] = {}
        collisions = []
        # (output folder, stem) -> [(file, output folder, derivative-skip
        # applies)] — the children's modes 6/7 leave out the JXLs that carry a
        # jxlphoto-derived marker (B2); the mirror re-checks only the files
        # that collide, after the walk.
        _members: Dict[Tuple[str, str], List[Tuple[Path, Path, bool]]] = {}
        _derived_capable = _child is not None and (
            _child.__name__ in ('jxl_tiff_decoder', 'jxl_recompressor')
            or (_child.__name__ == 'jxl_jpeg_transcoder' and origin == 'jxl'))

        def _record_output(f: Path, out_folder, rf: str, rt: str) -> None:
            """Collision bookkeeping for one resolved source file."""
            if out_folder is None:
                return
            key = os.path.normcase(str(out_folder))
            seen = by_dest.setdefault(key, {})
            stem = _stem_key(f, rf, rt) if _child is not None else os.path.normcase(f.stem)
            prev = seen.get(stem)
            if prev is None:
                seen[stem] = f
            elif os.path.normcase(str(prev)) != os.path.normcase(str(f)):
                collisions.append((prev, f, out_folder))
        # The child resolvers WARN on modes 4/5 (outside-tree outputs, missing
        # suffix token). The real run emits those lines itself — duplicated
        # here, one per scanned file, they are pure noise.
        _child_logger_was_disabled = None
        # The cmd builder passes marker/subfolder as CLI flags, which the children
        # apply to these same module globals — mirror that, and PUT THEM BACK.
        # They used to be assigned and never restored, so a manifest run leaked
        # its marker and subfolder into every later in-process use of that child
        # for the rest of the menu session (the delete preview's count among
        # them, which made it depend on what had been run before it).
        # The context enters PER ENTRY (row_export): an entry with its own
        # Export* columns must resolve with those, not the run's — and it must
        # EXIT between entries, because _with_child_marker only sets non-empty
        # values, so a row with subfolder None left inside an outer context
        # would inherit the outer value instead of the child's default.
        if _child is not None:
            _child_logger_was_disabled = _child.logger.disabled
            _child.logger.disabled = True
        try:
            for ei, (source, dest_path, mode) in enumerate(manifest_entries):
                if not dest_path:
                    continue
                _m, _s, _j = (row_export[ei] if row_export is not None
                              else (export_marker, export_subfolder,
                                    export_jxl_folder))
                # This entry's exclusion (ExcludeFolders column, or None for
                # a legacy manifest), mirrored into the child's
                # EXCLUDE_FOLDERS global AND applied on the walk below — the
                # walk never passes through the child's finders, where the
                # filter otherwise lives.
                _excl_eff = (row_excludes[ei] if row_excludes is not None
                             else None)
                # ...but ONLY where the child honors it: the cmd builder emits
                # --exclude-folders for the tiff<->jxl directions alone, so
                # anywhere else this walk must see every file the child will
                # process. Filtering in another direction hid REAL collisions
                # (a stale last_exclude_folders replayed into a jxl2jxl
                # preset shrank the walk below the recompressor's file set).
                if (origin, dest) not in (('tiff', 'jxl'), ('jxl', 'tiff')):
                    _excl_eff = None
                _excl_names = tuple(n.strip().lower() for n in _excl_eff.split(';')
                                    ) if _excl_eff else ()
                _excl_names = tuple(n for n in _excl_names if n)
                with (_with_child_marker(_child, _m, _s, _j, _excl_eff)
                      if _child is not None else nullcontext()):
                    # The row's own RenameFrom/RenameTo, when the manifest carries
                    # the columns; the run-wide pair is the fallback.
                    rf, rt = (row_renames[ei] if row_renames is not None
                              else (rename_from, rename_to))
                    # Per-entry output-tree recording for the cross-entry guard
                    # (only when a sink was handed in).
                    _entry_outs = (cross_sink.setdefault(ei, set())
                                   if cross_sink is not None else None)
                    src_root = Path(source)
                    try:
                        if src_root.is_file():
                            # A file Source is a legitimate manifest row, and the
                            # cross-entry collision guard must see it too: it used
                            # to be skipped here, silently. Resolve it exactly like
                            # the folder walk resolves one file (parent as root).
                            if resolver is None or mode is None:
                                _file_out = Path(dest_path)
                            elif _skip_check is not None and _skip_check(src_root, src_root.parent, mode):
                                _file_out = None
                            else:
                                _file_out = resolver(src_root, mode, src_root.parent, dest_path, rf, rt)
                            if _file_out is not None:
                                _record_output(src_root, _file_out.parent, rf, rt)
                                if _entry_outs is not None:
                                    _entry_outs.add(os.path.normcase(
                                        os.path.abspath(str(_file_out.parent))))
                            continue
                        if not src_root.is_dir():
                            continue
                    except OSError:
                        continue
                    # Legacy manifests carry no Mode cell: the child detects the mode
                    # per folder downstream, so the flat Destination scan is the best
                    # the wrapper can do there (the old behavior for every entry).
                    # Modes 6/7 with no explicit subfolder: the child leaves out
                    # the derivatives (B2). Read inside _with_child_marker, so
                    # the row's own subfolder is the one seen here.
                    _sub_now = (getattr(_child, 'EXPORT_JPEG_SUBFOLDER', '')
                                if _child is not None
                                and _child.__name__ == 'jxl_jpeg_transcoder'
                                else getattr(_child, 'EXPORT_JXL_SUBFOLDER', ''))
                    _derived_skip = bool(_derived_capable and mode is not None and (
                        mode == 6 or (mode == 7 and not _sub_now)))
                    if resolver is None or mode is None:
                        try:
                            files = sorted(src_root.iterdir())
                        except OSError:
                            continue
                        # Same exclusion filter as the resolver branch (already
                        # gated to the tiff<->jxl directions above): a legacy
                        # TIFF manifest still gets --exclude-folders from the
                        # cmd builder, so the child skips those files while
                        # this flat scan counted them — a phantom abort.
                        if _excl_names:
                            files = [f for f in files
                                     if not _path_excluded_below_root(
                                         f, src_root, _excl_names)]
                        outputs = ((f, Path(dest_path)) for f in files)
                    else:
                        # This walk is the slow part on large trees (recursive glob
                        # + per-file stat + resolver per source file) — say so.
                        if RICH_AVAILABLE and console:
                            console.print(f"[dim]Collision check: scanning {_rich_safe(src_root)} ...[/dim]")
                        else:
                            print(f"Collision check: scanning {src_root} ...")
                        try:
                            files = sorted(src_root.rglob('*') if mode >= 2 else src_root.iterdir())
                        except OSError:
                            continue

                        def _resolve_all(files, mode=mode, src_root=src_root, dest_path=dest_path,
                                         rf=rf, rt=rt, _excl_names=_excl_names):
                            for f in files:
                                # Filter BEFORE resolving: the child resolvers are
                                # only meaningful for real source files, and running
                                # them on directories/sidecars is wasted work.
                                try:
                                    if not f.is_file() or f.suffix.lower() not in origin_exts:
                                        continue
                                except OSError:
                                    continue
                                # The entry's --exclude-folders: the walk never
                                # passes through the child's finders, so the
                                # ONLY place the exclusion can bite is here.
                                # Skipping an excluded file before resolving is
                                # what keeps a flat-output row from aborting on
                                # a collision the run would never produce
                                # (the file is not even processed).
                                if _excl_names and _path_excluded_below_root(
                                        f, src_root, _excl_names):
                                    continue
                                if _skip_check is not None and _skip_check(f, src_root, mode):
                                    continue
                                out = resolver(f, mode, src_root, dest_path, rf, rt)
                                # out is None for files the child skips (e.g. outside
                                # the export marker in modes 6/7).
                                yield f, (out.parent if out is not None else None)

                        outputs = _resolve_all(files)
                    for f, out_folder in outputs:
                        try:
                            if not f.is_file() or f.suffix.lower() not in origin_exts:
                                continue
                        except OSError:
                            continue
                        if out_folder is None:
                            continue
                        if _entry_outs is not None:
                            _entry_outs.add(os.path.normcase(
                                os.path.abspath(str(out_folder))))
                        key = os.path.normcase(str(out_folder))
                        seen = by_dest.setdefault(key, {})
                        # Outputs are named from the stem, so a stem clash is a clash
                        # whatever the target extension is. normcase (not .lower()):
                        # case-sensitive filesystems treat Foto.tif/foto.tif as
                        # distinct files and must NOT collide. The recompressor's
                        # --rename-from renames that stem at planning time.
                        stem = (_stem_key(f, rf, rt) if _child is not None
                                else os.path.normcase(f.stem))
                        _members.setdefault((key, stem), []).append(
                            (f, out_folder, _derived_skip))
                        prev = seen.get(stem)
                        if prev is None:
                            seen[stem] = f
                        elif os.path.normcase(str(prev)) != os.path.normcase(str(f)):
                            collisions.append((prev, f, out_folder))
            if collisions and _derived_capable:
                collisions = self._without_derivative_collisions(
                    _child, collisions, _members)
            if _child is not None and _child.__name__ == 'jxl_tiff_encoder':
                collisions.extend(self._split_page_collisions(
                    _child, by_dest, page_options or {}))
        finally:
            if _child is not None:
                _child.logger.disabled = _child_logger_was_disabled
        return collisions

    @staticmethod
    def _without_derivative_collisions(child, collisions: List,
                                       members: Dict) -> List:
        """`collisions` with the derivatives the child leaves out (B2: modes
        6/7, a jxlphoto-derived marker) taken out of each colliding group.
        Only the files that collide are read; when the read fails, nothing is
        taken out (the collision stays reported — the safe side)."""
        groups = {k: ms for k, ms in members.items() if len(ms) > 1}
        files = sorted({str(f) for ms in groups.values() for f, _o, el in ms if el})
        if not files:
            return collisions
        try:
            if child.__name__ == 'jxl_tiff_decoder':
                marks = child._read_multipage_markers_batch([Path(f) for f in files])
                derived = {f for f in files if (marks.get(f) or {}).get('derived')}
            else:
                labels = child._read_derived_markers_batch([Path(f) for f in files])
                derived = {f for f in files if labels.get(f)}
        except Exception:
            return collisions
        if not derived:
            return collisions
        affected = {k for k, ms in groups.items()
                    if any(el and str(f) in derived for f, _o, el in ms)}
        if not affected:
            return collisions
        keep = [c for c in collisions
                if not any(os.path.normcase(str(c[1])) == os.path.normcase(str(f))
                           for k in affected for f, _o, _el in groups[k])]
        for k in sorted(affected):
            live = [(f, o) for f, o, el in groups[k] if not (el and str(f) in derived)]
            for f, o in live[1:]:
                if os.path.normcase(str(live[0][0])) != os.path.normcase(str(f)):
                    keep.append((live[0][0], f, o))
        return keep

    @staticmethod
    def _split_page_collisions(enc, by_dest: Dict[str, Dict[str, Path]],
                               page_options: Dict) -> List:
        """The split pages of _manifest_output_collisions (see page_options
        there): in each output folder, a stem shaped like a page of ANOTHER
        stem there (x_page1, x_page2_thumbnail, x_thumbnail next to x) is a
        collision when the encoder's plan for x's TIFF writes that name."""
        mp = (page_options.get('multipage_mode') or enc.MULTIPAGE_TIFF_MODE).lower()
        if mp == 'ignore':
            return []          # page 0 only: the stem scan already covers it
        tm = page_options.get('thumbnail_mode') or enc.THUMBNAIL_MODE
        suffix = page_options.get('thumbnail_suffix')
        if suffix is None:
            suffix = enc.THUMBNAIL_SUFFIX
        page_re = re.compile(r"^(?P<base>.+?)(?:_page\d+)?(?:"
                             + re.escape(os.path.normcase(suffix)) + r")?$")
        found = []
        for folder, seen in by_dest.items():
            planned: Dict[str, set] = {}
            for stem, f in seen.items():
                m = page_re.match(stem)
                base = m.group('base') if m else stem
                if base == stem or base not in seen:
                    continue
                owner = seen[base]
                if os.path.normcase(str(owner)) == os.path.normcase(str(f)):
                    continue
                if base not in planned:
                    try:
                        real, thumbs, _info = enc._analyze_tiff_pages(Path(owner))
                        names = enc._planned_page_names(
                            Path(owner).stem, real, thumbs, mp, tm, suffix,
                            page_info=_info)
                    except Exception:
                        names = []     # unreadable: the child reports it
                    planned[base] = {os.path.normcase(os.path.splitext(n)[0])
                                     for n in names}
                if stem in planned[base]:
                    found.append((owner, f, folder))
        return found

    @staticmethod
    def _manifest_source_overlaps(manifest_entries: List) -> List:
        """Find manifest entries whose Source folders are equal or nested.

        Two entries covering the same tree re-process the same files as two
        SEPARATE child processes: same outputs written twice, decided by
        sync-mtime luck — and the collision guard above deliberately ignores
        a file compared with itself, so it cannot see this.

        NESTING only matters when the OUTER entry's mode recurses into the inner
        one. Modes 0 and 1 are flat in every child, so `root` next to
        `root/sub1` there is not an overlap at all — flagging it made the
        wrapper warn about (and, unattended, REFUSE) the mode-0/1 manifests its
        own Auto Mode generates whenever files sit in the scan root.
        Identical Sources are always an overlap, whatever the mode.
        A legacy manifest with no Mode cell (None) counts as recursive: the mode
        is detected per folder downstream, so this fails closed.

        Returns a list of (source_a, source_b) tuples.
        """
        def _recurses(mode) -> bool:
            return mode is None or mode in _RECURSIVE_MANIFEST_MODES

        roots = [(source, os.path.normcase(os.path.abspath(source)), mode)
                 for source, _dest, mode in manifest_entries]
        overlaps = []
        for i, (src_a, a, mode_a) in enumerate(roots):
            for src_b, b, mode_b in roots[i + 1:]:
                if a == b:
                    overlaps.append((src_a, src_b))
                elif a.startswith(b + os.sep):
                    # b CONTAINS a: a problem only if b's child recurses into it
                    if _recurses(mode_b):
                        overlaps.append((src_a, src_b))
                elif b.startswith(a + os.sep):
                    if _recurses(mode_a):
                        overlaps.append((src_a, src_b))
        return overlaps

    @staticmethod
    def _manifest_output_source_collisions(manifest_entries: List,
                                           entry_out_folders: Dict[int, Set[str]]) -> List:
        """Cross-entry check the two guards above cannot make: one entry's
        OUTPUT folder is another entry's SOURCE.

        _manifest_output_collisions compares output x output;
        _manifest_source_overlaps compares Source x Source. Neither sees entry B
        whose Source IS the folder entry A writes its outputs to — and that
        folder usually does not even EXIST at planning time (A creates it during
        the run), so no disk-level check can see it either. B then processes A's
        fresh outputs as its inputs: for the recompressor that is a lost
        generation on files that were just written; for every direction it is a
        run whose input set silently depends on another entry having run first.

        The audit case: A (mode 1) recompresses G:\\lib into
        G:\\lib\\recompressed_jxl; B's Source IS that folder (mode 0). A is
        FLAT, so the source-overlap guard does not flag B's Source nested inside
        A's Source — and B's Source did not exist when the manifest was written.

        `entry_out_folders` is the per-entry set of resolved output folders
        recorded by _manifest_output_collisions during its walk — the same
        notion of "output tree" the children's own resolvers produce, not a
        guess from the Destination cells (which modes 1/3/4/5/6/7 ignore).

        Flagging rules mirror _manifest_source_overlaps: equal paths are always
        flagged, whatever B's mode; a folder of A's that merely sits INSIDE B's
        Source only matters when B recurses into it (a flat B reads direct
        children only, so nested outputs are unreachable for it). A B.source
        strictly BELOW one of A's output folders is deliberately NOT flagged:
        every child writes its outputs DIRECTLY into its output folders, so a
        subfolder of one never receives outputs — flagging it would only warn
        about harmless disjoint trees.

        Returns a list of (source_a, source_b, out_folder) tuples, at most one
        per (A, B) pair.
        """
        def _recurses(mode) -> bool:
            return mode is None or mode in _RECURSIVE_MANIFEST_MODES

        entries = [(s, os.path.normcase(os.path.abspath(str(s))), m)
                   for s, _d, m in manifest_entries]
        found = []
        seen_pairs: Set[Tuple[str, str]] = set()
        for i, outs in (entry_out_folders or {}).items():
            if i < 0 or i >= len(entries):
                continue
            src_a = entries[i][0]
            for out in outs:
                for j, (src_b, norm_b, mode_b) in enumerate(entries):
                    if j == i:
                        continue
                    if norm_b != out and not (out.startswith(norm_b + os.sep)
                                              and _recurses(mode_b)):
                        continue
                    key = (os.path.normcase(os.path.abspath(str(src_b))), out)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    found.append((src_a, src_b, out))
        return found

    def _append_provenance_flags(self, cmd: List, advanced: Dict,
                                 origin: str, dest: str) -> None:
        """Emit --provenance (and --no-adopt-scan) for the child about to run.

        One place instead of six: the six emission sites had already drifted —
        the decoder's two appended --no-adopt-scan, the transcoder's two did
        not, and all six passed `adopt` straight through to scripts that only
        declare path/content.

        A stored `adopt` (saved session, preset, or last_provenance carried over
        from a TIFF->JXL run) is downgraded to `path` rather than emitted, and
        the downgrade is announced. Downgrading is the SAFE direction: `path` is
        the strict check, so the worst case is a refusal that keeps every source
        — where emitting adopt is argparse exit 2, which converts nothing and,
        in a manifest, kills every remaining entry.
        """
        pv = advanced.get('provenance')
        if not pv:
            return
        if pv == 'adopt' and not _supports_provenance_adopt(origin, dest):
            msg = (f"--provenance adopt is only available for {('TIFF -> JXL').upper()} "
                   f"runs (only the encoder can verify the pairing before adopting it). "
                   f"This {origin.upper()} -> {dest.upper()} run uses the strict 'path' "
                   f"check instead: outputs with no record are REFUSED, not adopted, and "
                   f"nothing is deleted for them.")
            if RICH_AVAILABLE and console:
                console.print(f"[yellow]{_rich_safe(msg)}[/yellow]")
            else:
                print(f"WARNING: {msg}")
            pv = 'path'
        cmd.extend(['--provenance', pv])
        # Only the encoder has this flag, and it is inert without adopt.
        if pv == 'adopt' and advanced.get('adopt_scan') is False:
            cmd.append('--no-adopt-scan')

    def _append_overwrite_guard_flag(self, cmd: List, advanced: Dict) -> None:
        """--no-overwrite-guard when the run turned the guard off. Never with
        a delete: the child refuses that combination (exit 2) and the
        wizard does not offer it, so a hand-edited preset fails loudly."""
        if advanced.get('overwrite_guard') is False:
            cmd.append('--no-overwrite-guard')

    def _warn_exclude_folders_unsupported(self, origin: str, dest: str,
                                          value: str, at_most_once: bool = False) -> None:
        """Warn that --exclude-folders does not exist for this direction.

        Only the TIFF encoder and decoder accept the flag; the recompressor
        and the transcoder do not, so the wrapper omits it there and says so
        instead of silently ignoring the user's setting.

        `at_most_once` is for the MANIFEST builder, which runs once per entry:
        a run-scoped set keyed by direction keeps a 30-entry manifest from
        printing 30 copies of the same line.
        """
        if at_most_once:
            _warned = self.__dict__.setdefault('_exclude_folder_warned', set())
            if (origin, dest) in _warned:
                return
            _warned.add((origin, dest))
        _msg = (f"--exclude-folders is not supported for "
                f"{origin.upper()} -> {dest.upper()} runs; the "
                f"ExcludeFolders setting ({value}) is IGNORED here.")
        if RICH_AVAILABLE and console:
            console.print(f"[yellow]{_rich_safe(_msg)}[/yellow]")
        else:
            print(f"WARNING: {_msg}")

    def build_child_cmd(self, script: str, origin: str, dest: str, source: str,
                        dest_path: Optional[str], mode: int, workers: int,
                        workflow: Dict, advanced: Dict, *, manifest: bool) -> List:
        """The child's argv for one run — the direct run (execute_workflow)
        and every manifest entry (_build_manifest_entry_cmd) alike.

        Every flag comes from REGISTRY: the simple ones through _emit_opt, the
        ones with a rule through the _policy_* methods (and the two older
        _append_* helpers), in a fixed order; the expert flags go LAST. The
        output positional sits right after the input: appending it after the
        flags breaks argparse on Python < 3.12.7 (gh-59317). `manifest`
        selects the manifest-only rules: --summary-json, the at-most-once
        exclude warning, the derivative row's suppressed delete and the
        transcoder's rename on its lossy decode (the direct wizard refuses
        those combinations before it gets here)."""
        ctx = _CmdCtx(origin, dest, source, dest_path, mode, workers, workflow,
                      advanced, manifest, self.config.config.export_marker)
        cmd = [sys.executable, script, source]
        if dest_path and not _dest_is_source_file(source, dest_path, mode):
            cmd.append(dest_path)
        for opt in REGISTRY:
            if opt.kind == "policy" or ctx.direction not in opt.directions:
                continue
            if opt.when is not None and not opt.when(ctx):
                continue
            cmd.extend(_emit_opt(opt, ctx))
        self._policy_exclude_folders(cmd, ctx)
        self._policy_conversion(cmd, ctx)
        self._policy_rename(cmd, ctx)
        self._policy_delete(cmd, ctx)
        self._append_provenance_flags(cmd, ctx.advanced, origin, dest)
        self._append_overwrite_guard_flag(cmd, ctx.advanced)
        self._policy_format(cmd, ctx)
        _append_expert_flags(cmd, workflow.get('expert_flags'))
        return cmd

    def _policy_exclude_folders(self, cmd: List, ctx: "_CmdCtx") -> None:
        """--exclude-folders exists in the TIFF encoder and decoder only; any
        other direction warns that the setting is ignored — at most once per
        run for a manifest, whose builder runs once per entry."""
        excl = ctx.workflow.get('exclude_folders')
        if not excl:
            return
        if ctx.direction in ("tiff2jxl", "jxl2tiff"):
            cmd.extend(['--exclude-folders', excl])
        else:
            self._warn_exclude_folders_unsupported(ctx.origin, ctx.dest, excl,
                                                   at_most_once=ctx.manifest)

    def _policy_conversion(self, cmd: List, ctx: "_CmdCtx") -> None:
        """The transcoder's conversion type. --from-jpeg keeps a lossy
        JPEG -> JXL run off the PNGs in the folder (in mode 8 it would delete
        them); --from-jxl keeps the auto JXL -> JPEG run from transcoding the
        folder's JPEGs INTO JXL; the PNG output never transcodes even when a
        jbrd box is present."""
        if ctx.direction not in _TRANSCODER_DIRECTIONS:
            return
        cmd.extend({
            'transcode_lossless': ['--force-transcode'],
            'convert_lossy': ['--force-convert', '--from-jpeg'],
            'jxl_to_jpeg_auto': ['--from-jxl'],
            'jxl_to_jpeg_lossless': ['--force-transcode', '--decode'],
            'jxl_to_jpeg_force': ['--force-convert', '--decode'],
            'jxl_to_png': ['--force-convert', '--decode'],
        }.get(ctx.conv_type, []))

    def _policy_rename(self, cmd: List, ctx: "_CmdCtx") -> None:
        """--rename-from/--rename-to, always as a pair. The recompressor plans
        the rename up front; the transcoder applies it on its lossy decode
        only (the bit-exact recovery keeps the original name), and only the
        manifest route passes it there."""
        rf = ctx.advanced.get('rename_from')
        if not rf:
            return
        if ctx.direction == "jxl2jxl" or (ctx.manifest and ctx.decode_lossy):
            cmd.extend(['--rename-from', rf, '--rename-to',
                        ctx.advanced.get('rename_to') or ''])

    def _policy_delete(self, cmd: List, ctx: "_CmdCtx") -> None:
        """--delete-source and what rides on it. The wrapper has charged the
        HHMM token, so --delete-confirm-off goes with it: without it the
        child would ask again on an invisible stdin prompt and the run would
        appear to hang. A recompressor run that replaces its sources in
        place gets --delete-confirm-off as well, for the same reason (a
        headless run would exit 3 after doing nothing). --verify-roundtrip
        and --delete-skipped only mean something alongside --delete-source.

        Manifest transcoder rows: a derivative row (colour conversion,
        resize, sharpening) never deletes its source, so the flag is
        suppressed for THAT row (W6 of the 2026-10-08 audit: an sRGB JPEG
        must not delete the 16-bit master), said out loud for --icc-profile."""
        adv = ctx.advanced
        if ctx.direction == "jxl2jxl" and _recompress_entry_in_place(
                ctx.source, ctx.dest_path, ctx.mode):
            cmd.append('--delete-confirm-off')
        delete = bool(adv.get('delete_source'))
        if delete and ctx.manifest and ctx.direction in _TRANSCODER_DIRECTIONS:
            if ctx.workflow.get('icc_profile') and not ctx.derivative:
                _note = ("Colour conversion (--icc-profile) writes a DERIVATIVE: the "
                         "originals are NOT deleted on this run.")
                if RICH_AVAILABLE and console:
                    console.print(f"[yellow]{_rich_safe(_note)}[/yellow]")
                else:
                    print(_note)
            delete = not ctx.derivative and not ctx.workflow.get('icc_profile')
            _never = _lossy_decode_never_deletes(ctx.workflow) if delete else None
            if _never:
                # T-B: the row converts, its sources stay (the child would exit 2).
                _note = f"No delete for this row: {_never}."
                if RICH_AVAILABLE and console:
                    console.print(f"[yellow]{_rich_safe(_note)}[/yellow]")
                else:
                    print(_note)
                delete = False
        if not delete:
            return
        cmd.extend(['--delete-source', '--delete-confirm-off'])
        if ctx.direction in ("tiff2jxl", "jxl2jxl") and adv.get('verify_roundtrip'):
            cmd.append('--verify-roundtrip')
        if adv.get('delete_skipped'):
            cmd.append('--delete-skipped')

    def _policy_format(self, cmd: List, ctx: "_CmdCtx") -> None:
        """The transcoder's output format, from the destination."""
        if ctx.direction not in _TRANSCODER_DIRECTIONS:
            return
        if ctx.dest == 'png':
            cmd.extend(['--format', 'png'])
        elif ctx.dest in ('jpeg', 'jpg'):
            cmd.extend(['--format', 'jpeg'])

    def _build_manifest_entry_cmd(self, script: str, source: str, dest_path: str, mode: int,
                                   origin: str, dest: str, workers: int,
                                   workflow: Dict, advanced: Dict) -> Optional[List]:
        """Build command line for a single manifest entry (build_child_cmd).
        An empty Destination is not sent: it would become Path('.') — the
        wrapper's CWD — downstream."""
        return self.build_child_cmd(script, origin, dest, source, dest_path, mode,
                                    workers, workflow, advanced, manifest=True)

    def _print_child_line(self, line: str) -> None:
        """Colorize one line of child-script output."""
        line = line.strip()
        if not line:
            return
        if RICH_AVAILABLE and console:
            # Escape external content: file/folder names may contain [ ],
            # which rich would parse as markup (crash or silent corruption).
            from rich.markup import escape as _escape
            eline = _escape(line)
            if "[OK]" in line or "✓" in line or "Processing" in line:
                console.print(f"  [green]{eline}[/green]")
            elif "[ERROR]" in line or "| ERROR |" in line or "Error" in line or "✗" in line:
                console.print(f"  [red]{eline}[/red]")
            elif "[WARNING]" in line or "| WARNING |" in line or "⚠" in line:
                console.print(f"  [yellow]{eline}[/yellow]")
            elif "DRY" in line or "simulation" in line.lower():
                console.print(f"  [blue]{eline}[/blue]")
            else:
                console.print(f"  {eline}")
        else:
            print(f"  {line}")

    def _parse_child_summary(self, line: str) -> Optional[Dict]:
        """Decode a ##JXLSUM## line into the dict the final block aggregates.

        Defensive on purpose: a malformed or half-flushed line must degrade to
        "this entry reported no summary" (the block says so) rather than blow
        up a run that already finished its real work.
        """
        try:
            payload = json.loads(line.lstrip()[len(CHILD_SUMMARY_PREFIX):])
        except (ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None

        def _as_int(v) -> int:
            # A wrong-typed field ("ok": "many") must degrade to 0, not raise:
            # this parser runs after the child's real work is done, and an
            # exception here kills the whole manifest run at the summary.
            try:
                return int(v or 0)
            except (ValueError, TypeError):
                return 0

        clean = {
            "ok": _as_int(payload.get("ok")),
            "overwritten": _as_int(payload.get("overwritten")),
            "skipped": _as_int(payload.get("skipped")),
            "errors": _as_int(payload.get("errors")),
            # Only the encoder reports this today; the others default to 0.
            "unreadable": _as_int(payload.get("unreadable")),
            "dry_run": bool(payload.get("dry_run")),
            "log": str(payload.get("log") or ""),
            "extras": {},
            "failures": [],
            "unreadable_files": [],
            "failures_truncated": bool(payload.get("failures_truncated")),
        }
        extras = payload.get("extras")
        if isinstance(extras, dict):
            clean["extras"] = {str(k): int(v) for k, v in extras.items()
                               if isinstance(v, (int, float))}
        for key in ("failures", "unreadable_files"):
            raw = payload.get(key)
            if isinstance(raw, list):
                clean[key] = [
                    (str(f.get("file", "?")), str(f.get("reason", "")))
                    for f in raw if isinstance(f, dict)
                ]
        return clean

    def _child_idle_timeout(self) -> int:
        """Seconds of silence before _stream_child stops a child: the
        config's child_idle_timeout_minutes, at least 5 minutes; 60 minutes
        when the config has no usable value."""
        cfg = getattr(getattr(self, "config", None), "config", None)
        minutes = getattr(cfg, "child_idle_timeout_minutes", None)
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes != minutes:
            minutes = ToolConfig.child_idle_timeout_minutes
        return int(max(5, minutes) * 60)

    def _stream_child(self, cmd: List, idle_timeout: Optional[int] = None) -> int:
        """Run a child script, streaming stdout live, with an IDLE deadline.

        The deadline resets every time the child prints a line: a long healthy
        batch (large collections at effort 7+) is never killed, while a child
        stuck on an interactive stdin prompt (no output) is detected and
        killed. (The previous absolute 1-hour wall-clock limit could kill
        healthy long batches mid-run.)

        The children print one line per COMPLETED file, so the idle window has
        to cover the slowest single file, not the slowest batch — a 750 MB
        multi-page drum scan at effort 9/10 is minutes of legitimate silence,
        and on slower hardware the old 30 min left little margin before a
        healthy encode was killed and blamed on a "hidden prompt".

        Exit code contract: 0 = success, 1 = some files failed, 2 = aborted,
        3 = user declined a confirmation. Returns -1 on timeout/launch failure.
        """
        import queue
        import threading

        if idle_timeout is None:
            idle_timeout = self._child_idle_timeout()
        # Cleared here rather than in _run_subprocess: the single-run path calls
        # _stream_child directly, so a stale usage error would mislabel the NEXT
        # run's safety abort — and a stale summary would report the PREVIOUS
        # run's numbers for a child that died before emitting its own.
        self._last_child_usage_error = None
        self._last_child_summary = None

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONUNBUFFERED": "1"}
            )
        except FileNotFoundError:
            self._print_error(f"Script not found: {cmd[1] if len(cmd) > 1 else cmd}")
            return -1
        except Exception as e:
            self._print_error(f"Error launching: {e}")
            return -1

        q = queue.Queue()

        def _reader():
            try:
                for line in process.stdout:
                    q.put(line)
            finally:
                q.put(None)  # EOF sentinel

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        deadline = time.time() + idle_timeout
        try:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    _kill_process_tree(process)
                    self._print_error(
                        f"No output for {idle_timeout // 60} min: process killed "
                        f"(hidden prompt, hang, or a single file slower than that "
                        f"— the child prints only on completed files)")
                    return -1
                try:
                    line = q.get(timeout=min(1.0, max(remaining, 0.1)))
                except queue.Empty:
                    if not reader.is_alive():
                        break
                    continue
                if line is None:
                    break
                # Progress resets the idle deadline
                deadline = time.time() + idle_timeout
                # The machine-readable summary is data for the end-of-run
                # block, not output: swallow it so the user never sees JSON.
                if line.lstrip().startswith(CHILD_SUMMARY_PREFIX):
                    self._last_child_summary = self._parse_child_summary(line)
                    continue
                # Kept AND printed: the user should see argparse's own words,
                # and the exit-2 reporting below needs to know it was argparse.
                _usage = _CHILD_USAGE_ERROR_RE.match(line.rstrip())
                if _usage:
                    self._last_child_usage_error = _usage.group(2)
                self._print_child_line(line)
        except KeyboardInterrupt:
            # Ctrl+C must also take the child down, not leave it running.
            _kill_process_tree(process)
            raise

        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            return -1
        return process.returncode

    def _run_subprocess(self, cmd: List) -> int:
        """Run subprocess and return its exit code.

        Exit code contract with the child scripts: 0 = success, 1 = some
        files failed, 2 = aborted, 3 = user declined a confirmation.
        -1 = the streaming wrapper killed the child (idle timeout), the
        launch failed, or the post-run wait timed out — no real child exit
        code exists, and a manifest run treats it as the same fatal class
        as exit 2.
        """
        # (_stream_child clears the per-run state, including this one.)
        return self._stream_child(cmd)

    def execute_workflow(self, workflow: Dict, status: Dict[str, bool]) -> bool:
        """Execute the workflow - Build command dynamically"""

        # Run-scoped state resets. `_exclude_folder_warned` documents itself
        # as "at most once per run" (the manifest builder keys its once-off
        # warning on it), but it lived on `self` forever: a second run of an
        # unsupported direction in the same menu session stayed mute. Every
        # execution path — direct and manifest (mode 99, dispatched below) —
        # funnels through here, so resetting at the entry covers both (F-7).
        self._exclude_folder_warned = set()

        # The checks every run gets before anything else (expert flags,
        # option rules): _GATE_RULES, path "common".
        if _job_gates(self, workflow, "common"):
            return False

        # Handle manifest mode (mode 99) — it resolves and checks its own script.
        if workflow.get('mode') == 99:
            return self._execute_manifest_workflow(workflow, status)

        origin = workflow['origin_format']
        dest = workflow['dest_format']

        # Resolve the child script UP FRONT, before any gate is charged: the
        # HHMM token below is a "you are about to delete" proof of presence,
        # and asking for it — or pre-creating a mode-2 output folder — on
        # behalf of a script that is not even installed is work done for a run
        # that can never start (the manifest path already checks first).
        if origin == 'tiff' and dest == 'jxl':
            script = str(SCRIPT_DIR / 'jxl_tiff_encoder.py')
        elif origin == 'jxl' and dest == 'tiff':
            script = str(SCRIPT_DIR / 'jxl_tiff_decoder.py')
        elif origin == 'jxl' and dest == 'jxl':
            script = str(SCRIPT_DIR / 'jxl_recompressor.py')
        else:
            script = str(SCRIPT_DIR / 'jxl_jpeg_transcoder.py')
        if not Path(script).exists():
            self._print_error(f"Script not found: {script}")
            self._print_error("Ensure scripts are in the same folder as jxl_photo_v2.py")
            return False

        # Derivatives, degraded decodes, the lossy decode (T-B), the lossless
        # recovery's rename: _GATE_RULES, path "direct", BEFORE the HHMM
        # token is charged for a run the child would reject with exit 2.
        if _job_gates(self, workflow, "direct"):
            return False

        # Lossy + delete_skipped: the one combination with no provenance of any
        # kind. (Only the lossy TRANSCODER directions, `_LOSSY_CONVERSIONS`; the
        # recompressor proves provenance on its skipped path now.) Applied here
        # so repeats and presets are gated too, not just [D].
        self._confirm_lossy_delete_skipped(workflow)

        # Delete gate, at EXECUTION time (was Step 4): the user has already had
        # the chance to pick dry-run, and a simulation never charges the HHMM
        # token.
        #
        # Deliberately NOT keyed on the mode. Deleting is orthogonal to layout —
        # the children honour --delete-source in every mode — and the cmd
        # builder appends `--delete-source --delete-confirm-off` for any mode,
        # so a mode test here would suppress the ONLY confirmation in the chain.
        # (It used to be `and mode == 8`, which held only because the children
        # then ignored the flag outside mode 8.)
        _flag_delete = _flags_request_delete(workflow.get('expert_flags'))
        _adv_delete = bool(workflow.get('advanced_options', {}).get('delete_source'))
        # JXL -> JXL in modes 0/8 REPLACES the source files with the
        # recompressed versions — as irreversible as a delete, so the same
        # proof-of-presence gate applies. (The wrapper only ever passes an
        # output positional to the recompressor in mode 2, so mode 0 is
        # always in place here.)
        _in_place_recompress = (origin == 'jxl' and dest == 'jxl'
                                and workflow.get('mode') in (0, 8))
        if not workflow.get('dry_run') and (_flag_delete or _adv_delete
                                            or _in_place_recompress):
            if _in_place_recompress and not (_flag_delete or _adv_delete):
                _confirmed = self._confirm_in_place_replace()
            else:
                _confirmed = self._confirm_archive_mode()
            if not _confirmed:
                return False

        mode = workflow['mode']
        input_dir = workflow['input_dir']
        workers = workflow['workers']
        advanced = workflow.get('advanced_options', {})

        # `script`, `origin` and `dest` were resolved up front, before the gates.
        # Mode 2: flat output folder. The child creates it itself; it is only
        # pre-created for a real run, so a dry run leaves no trace on disk.
        output_dir = None
        if mode == 2:
            output_dir = workflow.get('mode_config', {}).get('output_dir') or None
            if output_dir and not workflow.get('dry_run'):
                try:
                    Path(output_dir).mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    self._print_error(f"Cannot create output folder {output_dir}: {e}")
                    return False
        cmd = self.build_child_cmd(script, origin, dest, input_dir, output_dir, mode,
                                   workers, workflow, advanced, manifest=False)

        if RICH_AVAILABLE and console:
            console.print(f"\n[bold cyan]Executing:[/bold cyan]")
            console.print(f"[dim]{_rich_safe(' '.join(_display_cmd(cmd)))}[/dim]\n")
        else:
            print(f"\nExecuting: {' '.join(_display_cmd(cmd))}\n")

        returncode = self._stream_child(cmd)

        if returncode == 0:
            self._print_success("✓ Conversion completed successfully!\n")
            return True
        elif returncode == 3:
            self._print_error("\n✗ Conversion cancelled (confirmation declined)")
            return False
        elif returncode == 2:
            if self._last_child_usage_error:
                self._print_error(
                    f"\n✗ The command line was rejected by {Path(script).name}: "
                    f"{self._last_child_usage_error}")
                self._print_error(
                    "  Nothing ran and nothing was touched. This is a WRAPPER bug — it "
                    "built a command this script does not accept — not a problem with "
                    "your files. Please report it with the 'Executing:' line above.")
            else:
                self._print_error("\n✗ Aborted by a safety check (see log above — e.g. duplicate output destinations or output/input collisions)")
            return False
        else:
            self._print_error(f"\n✗ Conversion failed (code {returncode})")
            return False

    @staticmethod
    def _describe_session(session: Dict) -> str:
        """One-line summary of a stored workflow, for the preset list."""
        origin = (session.get('last_origin_format') or '?').upper()
        dest = (session.get('last_dest_format') or '?').upper()
        # Coerced like _run_saved_session does: a mode stored as the NUMBER 99
        # (or as "99.0" by a hand edit) still has to read as a manifest here,
        # or the row advertises "mode 99" for a run that replays a CSV.
        raw_mode = session.get('last_output_mode')
        mode = _session_int(session, 'last_output_mode', -1)
        bits = [f"{origin}->{dest}"]
        if mode == 99:
            manifest = session.get('last_manifest_path')
            bits.append(f"manifest: {Path(manifest).name}" if manifest else "manifest: (missing)")
        else:
            bits.append(f"mode {mode if mode >= 0 else (raw_mode or '?')}")
            if session.get('last_input_dir'):
                bits.append(str(session['last_input_dir']))
        bits.append(f"workers {_session_int(session, 'last_workers', 4)}")
        # Which number this workflow is actually steered by, decided by the
        # DIRECTION — not by which field happens to be set. save_last_session
        # only overwrites last_distance when a run supplies one, so a JXL->JPEG
        # preset carries the distance of whatever TIFF run came before it and
        # used to advertise "d=0.05" for a decode that never reads it.
        conv = session.get('last_conversion_type') or ''
        distance_driven = (session.get('last_origin_format') == 'tiff'
                           or conv in ('convert_lossy', 'jxl_recompress'))
        # ...and quality only where the child actually receives --quality.
        # Without the direction test, a JXL->TIFF preset advertised the q= of
        # whatever JPEG run came before it (save_last_session only overwrites
        # last_quality when a run supplies one) — a knob the decoder never reads.
        # JPEG->JXL does NOT get --quality (lossy uses --distance, lossless uses
        # none), so the old `origin == 'jpeg'` clause advertised a dead knob on
        # every JPEG preset.
        quality_driven = (session.get('last_origin_format') == 'jxl'
                          and session.get('last_dest_format') in ('jpeg', 'png'))
        distance = session.get('last_distance')
        if distance_driven and distance is not None:
            bits.append(f"d={_sane_distance(distance):g}")
        elif not distance_driven and quality_driven and session.get('last_quality') is not None:
            bits.append(f"q={session['last_quality']}")
        if session.get('last_exclude_folders'):
            bits.append(f"excl: {session['last_exclude_folders']}")
        return " | ".join(bits)

    @staticmethod
    def _clean_preset_name(raw: str) -> Optional[str]:
        """Reject names that would be unusable or would corrupt the config: the
        key ends up in JSON and in a menu row, so control characters and
        runaway lengths are cut here rather than at display time."""
        name = (raw or "").strip().strip('"\'')
        if not name or len(name) > 40:
            return None
        if any(ord(ch) < 32 for ch in name):
            return None
        return name

    def presets_menu(self, status: Dict[str, bool]) -> None:
        """Named snapshots of a workflow, so several recurring jobs (a nightly
        manifest sync, a one-off shoot) can coexist instead of overwriting each
        other in the single 'last workflow' slot."""
        while True:
            presets = dict(self.config.config.presets or {})
            names = sorted(presets)

            if RICH_AVAILABLE and console:
                if names:
                    table = Table(show_header=True, header_style="bold cyan", box=BOX_SIMPLE)
                    table.add_column("#", justify="right", style="dim")
                    table.add_column("Name", style="bold")
                    table.add_column("Workflow")
                    table.add_column("Saved", style="dim")
                    for i, name in enumerate(names, 1):
                        table.add_row(str(i), _rich_safe(name), _rich_safe(self._describe_session(_preset_session(presets[name]))),
                                      presets[name].get('saved_at', ''))
                    console.print(Panel(table, title="[bold]Presets[/bold]", border_style="green"))
                else:
                    console.print("\n[dim]No presets saved yet.[/dim]")
                console.print("[bold]\\[number][/bold] run  "
                              "[bold]\\[S][/bold] save last workflow as preset  "
                              "[bold]\\[D][/bold] delete  [bold]\\[B][/bold] back")
                choice = Prompt.ask("Presets").strip()
            else:
                print("\n--- Presets ---")
                if names:
                    for i, name in enumerate(names, 1):
                        print(f"  {i}. {name}")
                        print(f"     {self._describe_session(_preset_session(presets[name]))}")
                else:
                    print("  (none saved yet)")
                print("[number] run | [S] save last workflow as preset | [D] delete | [B] back")
                choice = input("Presets: ").strip()

            upper = choice.upper()
            if not choice or upper == "B":
                return

            if upper == "S":
                snapshot = self.config.session_snapshot()
                if not snapshot.get('last_input_dir'):
                    self._print_error("No workflow has been run yet — run one first, then save it here.")
                    continue
                if RICH_AVAILABLE and console:
                    console.print(f"[dim]{_rich_safe(self._describe_session(snapshot))}[/dim]")
                    raw_name = Prompt.ask("Preset name")
                else:
                    print(f"  {self._describe_session(snapshot)}")
                    raw_name = input("Preset name: ")
                name = self._clean_preset_name(raw_name)
                if not name:
                    self._print_error("Invalid name (empty, too long, or contains control characters).")
                    continue
                if name in presets:
                    if RICH_AVAILABLE and console:
                        overwrite = Confirm.ask(f"Preset '{_rich_safe(name)}' already exists. Overwrite?", default=False)
                    else:
                        overwrite = input(f"Preset '{name}' exists. Overwrite? [y/N]: ").strip().lower().startswith('y')
                    if not overwrite:
                        continue
                self.config.save_preset(name, snapshot)
                self._print_success(f"Preset '{name}' saved.")
                continue

            if upper == "D":
                if not names:
                    continue
                if RICH_AVAILABLE and console:
                    target = Prompt.ask("Delete which number?").strip()
                else:
                    target = input("Delete which number?: ").strip()
                if not target.isdigit() or not 1 <= int(target) <= len(names):
                    self._print_error("No preset with that number.")
                    continue
                name = names[int(target) - 1]
                if RICH_AVAILABLE and console:
                    sure = Confirm.ask(f"Delete preset '{_rich_safe(name)}'?", default=False)
                else:
                    sure = input(f"Delete preset '{name}'? [y/N]: ").strip().lower().startswith('y')
                if sure and self.config.delete_preset(name):
                    self._print_success(f"Preset '{name}' deleted.")
                continue

            if choice.isdigit() and 1 <= int(choice) <= len(names):
                name = names[int(choice) - 1]
                # Straight to the shared runner: a preset is exactly a stored
                # session, and it gets the same overwrite/sync and dry-run
                # questions as 'Repeat last workflow'.
                self._run_saved_session(_preset_session(presets[name]), status)
                return

            self._print_error("Not a valid option.")

    def _run_saved_session(self, session: Dict, status: Dict[str, bool],
                           answers: Optional[Dict] = None) -> bool:
        """Re-run a stored workflow: "Repeat last workflow" (a snapshot of the
        last_* config fields) and named presets (a snapshot saved under a name)
        both come through here, so the two can never drift apart.

        `session` is a plain dict keyed exactly like the last_* config fields.
        Overwrite/sync and dry-run are always re-asked: the stored run may have
        been a simulation, and inheriting that silently would turn a repeat into
        a real conversion (or vice versa) without a word.

        `answers` supplies those two decisions up front for `--run-preset`, the
        unattended path (Task Scheduler/cron), where there is nobody to ask.
        They are still explicit choices — passed as CLI flags, never inherited
        from the stored run. Returns True only when a run actually executed."""
        unattended = answers is not None

        # Everything below comes from hand-editable JSON with no type checking,
        # and goes on to pick a mode and build a command line. Check it before
        # printing anything: a corrupt value used to surface either as a raw
        # ValueError traceback (mode) or as the child's argparse rejecting a
        # command line the user never typed (workers/effort/quality).
        _corrupt = _session_number_error(session)
        if _corrupt:
            self._print_error(
                f"This saved workflow is corrupt: {_corrupt}.\n"
                f"Fix it in the settings file, or re-save the workflow from the menu.")
            return False

        last_dir = session.get('last_input_dir') or ""
        # Coerced, not just validated: see _session_int. This is also what makes
        # a mode stored as the NUMBER 99 (rather than the string) still read as
        # a manifest repeat below.
        last_mode = _session_int(session, 'last_output_mode', 0)
        last_workers = _session_int(session, 'last_workers', 4)
        last_staging = session.get('last_staging') or ""
        last_effort = _session_int(session, 'last_effort', 7)
        last_quality = _session_int(session, 'last_quality', 95)
        # Routed through the project's own clamp, which every other caller
        # already uses — the preset path was the one that read it raw and put
        # it straight on the cjxl command line.
        _raw_distance = session.get('last_distance')
        last_distance = None if _raw_distance is None else _sane_distance(_raw_distance)
        last_origin = session.get('last_origin_format') or "tiff"
        last_dest = session.get('last_dest_format') or ("jxl" if last_origin != "jxl" else "jpeg")
        last_conv_type = session.get('last_conversion_type') or ""
        last_d50_patch = (session.get('last_d50_patch')
                          or _child_default('jxl_tiff_encoder', 'd50_patch'))
        last_encode_tag = (session.get('last_encode_tag')
                           or _child_default('jxl_tiff_encoder', 'encode_tag'))

        # A manifest repeat re-reads the CSV instead of replaying a stored
        # copy of its rows, so edits made in Excel between runs take effect
        # — that is the whole point of repeating a manifest on a schedule.
        is_manifest_repeat = last_mode == 99
        manifest_path = session.get('last_manifest_path')
        manifest_entries = None
        manifest_row_options: List = []
        if is_manifest_repeat:
            if not manifest_path or not Path(manifest_path).exists():
                self._print_error(
                    f"The manifest for the last run is gone: {manifest_path or '(not saved)'}\n"
                    f"Use 'New workflow' to generate or pick another one.")
                return False
            manifest_entries = self._load_manifest_entries(
                manifest_path, last_origin, last_dest,
                row_options=manifest_row_options)
            if manifest_entries is None:
                # _load_manifest_entries already explained why.
                return False

        # Which knob the run is actually steered by — decided by the DIRECTION,
        # mirroring _describe_session (fix #319a): TIFF->JXL and the lossy
        # converts (JPEG->JXL lossy, JXL->JXL recompress) are distance-driven;
        # quality only where the child actually receives --quality (JXL->JPEG /
        # JXL->PNG). The old chain advertised "Quality" on EVERY JPEG->JXL
        # preset — a flag the child never receives — and hid Distance on
        # JXL->JXL, the parameter the recompression IS. Both mistakes sat right
        # above the "Proceed?" of the repeat/preset dialog.
        _distance_driven = (last_origin == 'tiff'
                            or last_conv_type in ('convert_lossy', 'jxl_recompress'))
        _quality_driven = (last_origin == 'jxl' and last_dest in ('jpeg', 'png'))

        if RICH_AVAILABLE and console:
            settings = [
                ["Manifest", f"{Path(manifest_path).name} ({len(manifest_entries)} entries)"]
                if is_manifest_repeat else ["Input folder", last_dir],
                ["Source", last_origin.upper()],
                ["Destination", last_dest.upper()],
                ["Mode", str(last_mode)],
                ["Workers", str(last_workers)],
                ["Effort", str(last_effort)],
            ]
            # Show the knob that actually steers this run (see above).
            if _distance_driven and last_distance is not None:
                settings.append(["Distance", f"{last_distance}"])
            elif not _distance_driven and _quality_driven and last_quality is not None:
                settings.append(["Quality", str(last_quality)])
            settings.append(["Staging", last_staging or "(none)"])
            # Surface silently-reapplied dangerous settings (they are NOT
            # re-asked on this path, so the user must see them here).
            _last_adv = session.get('last_advanced_options') or {}
            if _last_adv.get('delete_source'):
                settings.append(["Delete source", "ON"])
            if session.get('last_overwrite_guard') is False:
                settings.append(["Overwrite guard", "off"])
            if session.get('last_expert_flags'):
                settings.append(["Expert flags", session.get('last_expert_flags')])
            
            t = Table(box=BOX_SIMPLE, show_header=False, pad_edge=False)
            t.add_column("", style="cyan")
            t.add_column("")
            for row in settings:
                t.add_row(*(_rich_safe(c) for c in row))
            console.print(Panel(t, title="[bold]Last Workflow Settings[/bold]", border_style="green"))
        else:
            print("\n=== Last Workflow Settings ===")
            if is_manifest_repeat:
                print(f"  Manifest:     {Path(manifest_path).name} ({len(manifest_entries)} entries)")
            else:
                print(f"  Input folder: {last_dir}")
            print(f"  Source:       {last_origin.upper()}")
            print(f"  Destination:  {last_dest.upper()}")
            print(f"  Mode:         {last_mode}")
            print(f"  Workers:      {last_workers}")
            print(f"  Effort:       {last_effort}")
            # Same rule as the rich panel: the knob that actually steers this
            # run (see _distance_driven/_quality_driven above, mirror of
            # _describe_session).
            if _distance_driven and last_distance is not None:
                print(f"  Distance:     {last_distance}")
            elif not _distance_driven and _quality_driven and last_quality is not None:
                print(f"  Quality:      {last_quality}")
            print(f"  Staging:      {last_staging or '(none)'}")
            _last_adv = session.get('last_advanced_options') or {}
            if _last_adv.get('delete_source'):
                print(f"  Delete source: ON")
            if session.get('last_overwrite_guard') is False:
                print(f"  Overwrite guard: off")
            if session.get('last_expert_flags'):
                print(f"  Expert flags: {session.get('last_expert_flags')}")
            print()

        if is_manifest_repeat:
            # The manifest carries its own source/destination per entry, so
            # asking for an input folder here would be meaningless (and any
            # answer would be ignored).
            new_folder = last_dir
            input_path = Path(new_folder) if new_folder else Path.cwd()
        elif unattended:
            # Nobody to ask: the stored folder is the whole point of the preset.
            new_folder = last_dir
            input_path = Path(new_folder) if new_folder else Path.cwd()
            if not new_folder or not input_path.exists():
                self._print_error(f"The preset's input folder is gone: {new_folder or '(not saved)'}")
                return False
        else:
            if RICH_AVAILABLE and console:
                new_folder = Prompt.ask(f"\n[bold cyan]Input folder[/bold cyan]", default=last_dir).strip()
            else:
                new_folder = input(f"\nInput folder [{last_dir}]: ").strip()

            if not new_folder:
                new_folder = last_dir

            new_folder = _strip_surrounding_quotes(new_folder)
            input_path = Path(new_folder)
            if not input_path.exists():
                if RICH_AVAILABLE and console:
                    console.print(f"[red]Folder not found: {_rich_safe(new_folder)}[/red]")
                else:
                    print(f"Folder not found: {new_folder}")
                return False

        origin = last_origin

        if unattended:
            ow_choice = "1" if answers.get('overwrite') else "2"
        elif RICH_AVAILABLE and console:
            console.print("Existing file handling: [1] overwrite all | [2] sync (reconvert if newer)")
            ow_choice = Prompt.ask("If exists", choices=["1", "2"], default="2")
        else:
            ow_choice = input("If exists (1=overwrite, 2=sync) [2]: ").strip() or "2"

        if ow_choice == "1":
            overwrite, sync = True, False
        else:
            overwrite, sync = False, True

        # Ask dry-run explicitly — the original run may have been a
        # simulation, and hardcoding False would turn a repeat into a REAL
        # conversion without any notice.
        if unattended:
            dry_choice = bool(answers.get('dry_run'))
        elif RICH_AVAILABLE and console:
            dry_choice = Confirm.ask("Dry run? (simulate without converting)", default=False)
        else:
            dry_choice = input("Dry run? [y/N]: ").strip().lower().startswith('y')

        if unattended:
            # A destructive preset cannot run unattended (_GATE_RULES, path
            # "preset": delete in any mode or in the expert flags, or an
            # in-place recompression).
            if _job_gates(self, {}, "preset", session=session, dry_run=dry_choice,
                          origin=origin, dest=last_dest, mode=last_mode):
                return False
            proceed = True
        elif RICH_AVAILABLE and console:
            proceed = Confirm.ask(f"\n[bold]Proceed with this workflow?[/bold]", default=True)
        else:
            resp = input(f"\nProceed with this workflow? [Y/n]: ").strip().lower()
            proceed = not resp.startswith('n')

        if not proceed:
            return False

        # Separate distance (TIFF) and quality (JPEG) - don't mix!
        if origin == 'tiff':
            workflow = {
                'input_dir': new_folder,
                'mode': last_mode,
                'workers': last_workers,
                'staging': last_staging,
                'effort': last_effort,
                'distance': last_distance if last_distance is not None else 0.1,
                'overwrite_mode': ow_choice,
            }
        else:
            workflow = {
                'input_dir': new_folder,
                'mode': last_mode,
                'workers': last_workers,
                'staging': last_staging,
                'effort': last_effort,
                'quality': last_quality,
                'overwrite_mode': ow_choice,
            }
            if last_distance is not None:
                workflow['distance'] = last_distance

        if is_manifest_repeat:
            workflow['manifest_entries'] = manifest_entries
            workflow['manifest_row_options'] = manifest_row_options
            workflow['manifest_path'] = manifest_path

        if workflow['mode'] == 2:
            saved_out = (session.get('last_mode_config') or {}).get('output_dir')
            # Only reuse the saved output folder when the input folder is
            # the same as the saved session's; with a NEW input folder the
            # old destination would silently swallow the new outputs.
            if saved_out and new_folder == last_dir:
                workflow['mode_config'] = {'output_dir': saved_out}
            else:
                workflow['mode_config'] = {'output_dir': str(input_path.parent / "output")}
        else:
            # mode_config is mode-SPECIFIC: a stale export_subfolder from a
            # previous mode-7 run must not leak into mode 0/3/6 repeats
            # (it changes child behavior, e.g. the decoder-output filter).
            _keep = {6: ('export_marker', 'export_jxl_folder'),
                     7: ('export_marker', 'export_subfolder', 'export_jxl_folder'),
                     # Manifest repeat (99): the analyzer and the cmd builder
                     # take the marker from mode_config FIRST and only fall
                     # back to the global — so an empty mode_config made a
                     # preset saved with a custom marker run with whatever the
                     # CURRENT global marker is (or with none at all: the
                     # builder omits --export-marker when the global is the
                     # default _EXPORT). A saved field must be REPLAYED, not
                     # silently swapped for a different one; every other
                     # mode's fields pass through the _session_number_error
                     # validation up top instead of being discarded, and
                     # markers are strings, so preservation is the guard here.
                     99: ('export_marker', 'export_subfolder', 'export_jxl_folder')}
            _src = session.get('last_mode_config') or {}
            workflow['mode_config'] = {k: _src[k] for k in _keep.get(workflow['mode'], ()) if k in _src}

        workflow['origin_format'] = origin
        workflow['dest_format'] = last_dest
        # TOP-LEVEL, never inside the per-mode _keep of mode_config: folder
        # exclusion is not mode-specific (it filters discovery in every
        # mode), and the manifest's ExcludeFolders column overrides it per
        # row via _row_effective_options. Validated above by
        # _session_number_error.
        # CARRIED only for the directions whose child receives the flag
        # (tiff<->jxl, the cmd builder's emission gate): a stale
        # last_exclude_folders left over from a TIFF run replayed into a
        # jxl2jxl/jpeg preset used to shrink the collision walk below the
        # child's real file set (hiding a real collision) AND raise the
        # spurious "IGNORED" warning on runs that never asked for it (F-1,
        # part 2).
        workflow['exclude_folders'] = (
            session.get('last_exclude_folders')
            if (origin, last_dest) in (('tiff', 'jxl'), ('jxl', 'tiff'))
            else None)
        workflow['use_ram'] = (session.get('last_use_ram')
                               if session.get('last_use_ram') is not None
                               else _child_default('jxl_tiff_encoder', 'use_ram'))
        workflow['icc_profile'] = session.get('last_icc_profile')
        workflow['compression'] = (session.get('last_compression')
                                   or _child_default('jxl_tiff_decoder', 'compression'))
        # None = the child's own default (_bit_depth_or_default)
        workflow['bit_depth'] = session.get('last_bit_depth')
        workflow['dry_run'] = dry_choice
        workflow['add_preview'] = (session.get('last_add_preview')
                                   if session.get('last_add_preview') is not None
                                   else _child_default('jxl_tiff_decoder', 'add_preview'))
        fallback_advanced = {
            'overwrite': overwrite,
            'sync': sync,
            'd50_patch': last_d50_patch if origin == 'tiff' else None,
            'encode_tag': last_encode_tag if origin == 'tiff' else None,
            'multipage_mode': (session.get('last_multipage_mode')
                               or _child_default('jxl_tiff_encoder', 'multipage_mode')),
            'thumbnail_mode': (session.get('last_thumbnail_mode')
                               or _child_default('jxl_tiff_encoder', 'thumbnail_mode')),
            'thumbnail_suffix': (session.get('last_thumbnail_suffix')
                                 or _child_default('jxl_tiff_decoder' if origin == 'jxl'
                                                   else 'jxl_tiff_encoder', 'thumbnail_suffix')),
            'thumbnail_handling': (session.get('last_thumbnail_handling')
                                   or _child_default('jxl_tiff_decoder', 'thumbnail_handling')),
            'no_reconstruct_multipage': (
                bool(session.get('last_no_reconstruct_multipage'))
                if session.get('last_no_reconstruct_multipage') is not None
                else not _child_default('jxl_tiff_decoder', 'reconstruct_multipage')),
            'depth_policy': (session.get('last_depth_policy')
                             or _child_default('jxl_tiff_decoder', 'depth_policy')),
            'matrix': False,
            'basic': False,
            'none': False,
            'target_icc': None,
            'no_icc_cleanup': False,
        }
        workflow['advanced_options'] = dict(session.get('last_advanced_options') or fallback_advanced)
        workflow['extra_options'] = dict(session.get('last_extra_options') or {})
        # Ensure overwrite/sync reflect the choice just made in this dialog,
        # not a stale value from a previous session's last_advanced_options.
        workflow['advanced_options']['overwrite'] = overwrite
        workflow['advanced_options']['sync'] = sync
        # Its own config field, so a preset saved before the option existed (no
        # 'verify_roundtrip' key inside last_advanced_options) still gets it.
        # Only meaningful with delete_source, which the child enforces anyway.
        if session.get('last_verify_roundtrip'):
            workflow['advanced_options']['verify_roundtrip'] = True
        if session.get('last_delete_skipped'):
            workflow['advanced_options']['delete_skipped'] = True
        if session.get('last_provenance'):
            workflow['advanced_options']['provenance'] = session['last_provenance']
        if session.get('last_overwrite_guard') is False:
            workflow['advanced_options']['overwrite_guard'] = False
        if session.get('last_adopt_scan') is False:
            workflow['advanced_options']['adopt_scan'] = False
        workflow['expert_flags'] = session.get('last_expert_flags') or ''
        workflow['auto_mode_used'] = False
        # The manifest executor's overlap gate behaves differently when there
        # is nobody to ask (refuse instead of confirm).
        workflow['unattended'] = unattended
        
        # Use saved conversion type or fallback to defaults
        if last_conv_type:
            workflow['conversion_type'] = last_conv_type
        elif origin == 'jpeg':
            workflow['conversion_type'] = 'transcode_lossless'
        elif origin == 'tiff':
            workflow['conversion_type'] = 'jxl_tiff_encoder'
        elif origin == 'jxl' and last_dest == 'tiff':
            workflow['conversion_type'] = 'jxl_tiff_decoder'
        elif origin == 'jxl' and last_dest == 'jpeg':
            # No conversion type saved (old config): default to auto, which
            # recovers losslessly via jbrd when possible, instead of force
            # lossy, which would recompress everything.
            workflow['conversion_type'] = 'jxl_to_jpeg_auto'
        elif origin == 'jxl' and last_dest == 'png':
            workflow['conversion_type'] = 'jxl_to_png'
        elif origin == 'jxl' and last_dest == 'jxl':
            workflow['conversion_type'] = 'jxl_recompress'
        else:
            workflow['conversion_type'] = 'jxl_to_jpeg_force'

        # NOTE: no HHMM gate here — execute_workflow() has the single
        # execution-time gate (which also honors dry_run). A gate here
        # would ask for the token TWICE in a row (and the second prompt
        # could roll into the next minute and fail spuriously).

        return bool(self.execute_workflow(workflow, status))

    def _print_success(self, message: str) -> None:
        if RICH_AVAILABLE and console:
            console.print(f"[green]✓[/green] {_rich_safe(message)}")
        else:
            print(f"✓ {message}")

    def _print_error(self, message: str) -> None:
        if RICH_AVAILABLE and console:
            console.print(f"[red]✗[/red] {_rich_safe(message)}")
        else:
            print(f"✗ {message}")


# Where Explorer looks up a .jxl thumbnail handler (the IThumbnailProvider
# shell-extension GUID), per extension or under SystemFileAssociations.
_THUMBNAIL_HANDLER_KEYS = (
    r".jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}",
    r"SystemFileAssociations\.jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}",
)


_UPDATE_OWNER = "rsilvabr"
_UPDATE_TIMEOUT = 3          # seconds: a slow or absent network stays silent


def _update_repo() -> str:
    """The repository whose releases this copy follows: the Windows program
    (a frozen build) follows jxl-photo-exe, the scripts follow jxl-photo."""
    return "jxl-photo-exe" if getattr(sys, "frozen", False) else "jxl-photo"


def _version_key(text: Optional[str]):
    """(major, minor, patch, final) of "v2.22.0", "2.22.0_beta1" or
    "v2.22.0-exe1" (the exe's package revision is ignored: exe2 of the same
    release is not news here), or None. A beta sorts before its release."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(_beta\d+)?(?:-exe\d+)?$", (text or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if m.group(4) else 1)


def _latest_release_tag(repo: str, timeout: float = _UPDATE_TIMEOUT) -> Optional[str]:
    """The tag of the repository's latest release (GitHub leaves pre-releases
    out of "latest"), or None on any failure: no network, a timeout, a rate
    limit, an answer that is not what it should be. Sends no personal data:
    a generic User-Agent, no token."""
    import urllib.request
    url = f"https://api.github.com/repos/{_UPDATE_OWNER}/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "jxl-photo-update-check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(65536).decode("utf-8"))
        tag = data.get("tag_name") if isinstance(data, dict) else None
        return tag if isinstance(tag, str) and _version_key(tag) else None
    except Exception:
        return None


def _update_notice(config_manager, today: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """(the menu line, the release page URL) when a newer release than this
    copy is out, else None. Asks GitHub at most once a day (the answer is kept
    in the config for the rest of the day); off with check_for_updates =
    false in the config or JXLPHOTO_NO_UPDATE_CHECK=1."""
    cfg = config_manager.config
    if not cfg.check_for_updates or os.environ.get("JXLPHOTO_NO_UPDATE_CHECK"):
        return None
    from datetime import date
    today = today or date.today().isoformat()
    repo = _update_repo()
    if cfg.update_checked_on != today:
        tag = _latest_release_tag(repo)
        cfg.update_checked_on = today
        if tag:
            cfg.update_latest_seen = tag
        try:
            config_manager.save_config()
        except Exception:
            pass
    latest = cfg.update_latest_seen
    mine, theirs = _version_key(TOOLKIT_VERSION), _version_key(latest)
    if not mine or not theirs or theirs[:3] < mine[:3] or theirs <= mine:
        return None
    url = f"https://github.com/{_UPDATE_OWNER}/{repo}/releases/latest"
    return (f"A new release is out: {latest} (this copy is {TOOLKIT_VERSION}) — "
            f"[U] in the menu opens its page.", url)


def _jxl_thumbnail_tip() -> Optional[str]:
    """The menu's one-line tip when Windows Explorer has no thumbnail handler
    for .jxl (neither jxl-thumb nor Microsoft's JPEG XL extension), else None.

    One registry read, nothing written; any error means no tip, never a
    blocked menu. JXLPHOTO_THUMBNAIL_TIP replaces the text (a packaged build
    names its own install command); an empty value turns the tip off.
    """
    if os.name != "nt":
        return None
    try:
        import winreg
        for sub in _THUMBNAIL_HANDLER_KEYS:
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, sub) as key:
                    # Stock Windows 11 has the SystemFileAssociations key with
                    # an empty value: that registers nothing.
                    if str(winreg.QueryValue(key, None) or "").strip():
                        return None
            except OSError:
                continue
    except Exception:
        return None
    override = os.environ.get("JXLPHOTO_THUMBNAIL_TIP")
    if override is not None:
        return override.strip() or None
    return (f"Tip: Windows Explorer shows no thumbnails for JXL files on this PC. "
            f"jxl-thumb adds them: {JXL_THUMB_URL}")


def main():
    try:
        _main()
    except KeyboardInterrupt:
        # Ctrl+C anywhere — at a menu prompt, or in a child (which _stream_child
        # has already killed, and a manifest run has already summarized). Say so
        # instead of dumping a traceback; 130 is the conventional 128+SIGINT.
        print("\nCancelled (Ctrl+C).")
        sys.exit(130)
    except EOFError:
        # A prompt that read end-of-input: Ctrl+Z/Enter, a closed console, or
        # Ctrl+C on Windows, where input() can see the end of the line before
        # the KeyboardInterrupt arrives (a CPython race). Same as cancelling,
        # instead of a traceback in a window that closes with it.
        print("\nCancelled (end of input).")
        sys.exit(130)


def _main():
    parser = argparse.ArgumentParser(description="JXL Tools v2 - JPEG XL Processing with Auto Mode")
    parser.add_argument("--version", action="version",
                        version=f"{TOOLKIT_NAME} {TOOLKIT_VERSION}")
    parser.add_argument("--recheck", action="store_true", help="Force dependency recheck")
    parser.add_argument("--run-preset", metavar="NAME",
                        help="Run a saved preset without the menu (Task Scheduler / cron) and exit")
    parser.add_argument("--list-presets", action="store_true",
                        help="List saved presets and exit")
    parser.add_argument("--overwrite", action="store_true",
                        help="With --run-preset: overwrite existing outputs (default: sync, "
                             "reconvert only what is newer)")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --run-preset: simulate without converting")
    args = parser.parse_args()

    # These two are decisions, not preferences carried over from the stored run,
    # so they only mean something next to --run-preset. Silently ignoring a
    # stray --dry-run would be the worst outcome: a REAL conversion for someone
    # who believed they asked for a simulation.
    if (args.overwrite or args.dry_run) and not args.run_preset:
        parser.error("--overwrite and --dry-run only apply together with --run-preset")

    config = ConfigManager()
    checker = DependencyChecker(config)
    menu = InteractiveMenu(config, checker)

    force_check = args.recheck or not config.config.dependencies_checked
    # --list-presets is documented read-only: detect the tools so the status
    # bar is honest, but do not let the check write the discovered paths /
    # dependencies_checked stamp back into the config file (the check used to
    # rewrite it on EVERY initialization, listing included).
    status = checker.check_dependencies(force=force_check,
                                        persist=not args.list_presets)

    menu.display_status(status)

    # Scripts from different releases in one folder: each output records the
    # version of the script that wrote it, so a forgotten copy shows up in
    # the files' History. Warn now instead (never blocks the run).
    _mixed = _mixed_version_messages(SCRIPT_DIR)
    for _m in _mixed:
        print(f"WARNING: mixed versions — {_m}")
        logger.warning(f"Mixed versions: {_m}")
    if _mixed:
        print("         Copy all five scripts from the same release.")

    if args.list_presets:
        # Read-only: listing what is saved must work even on a machine with no
        # codecs installed (e.g. checking what a config carries before copying
        # it), so this runs BEFORE the cjxl/djxl gate below.
        presets = config.config.presets or {}
        if not presets:
            print("No presets saved. Run a job, then save it with [s] on its review (or from menu 3).")
        for name in sorted(presets):
            print(f"{name}\n    {menu._describe_session(_preset_session(presets[name]))}")
        sys.exit(0)

    if not status.get('cjxl') and not status.get('djxl'):
        print("\nERROR: cjxl/djxl not found!")
        sys.exit(1)

    if args.run_preset:
        presets = config.config.presets or {}
        if args.run_preset not in presets:
            available = ", ".join(sorted(presets)) or "(none saved)"
            print(f"ERROR: no preset named {args.run_preset!r}. Available: {available}")
            sys.exit(2)
        ok = menu._run_saved_session(
            _preset_session(presets[args.run_preset]), status,
            answers={'overwrite': args.overwrite, 'dry_run': args.dry_run})
        sys.exit(0 if ok else 1)

    # Menu runs only: a scheduled --run-preset log is no place for it.
    # A new release? Menu runs only, like the tip: --run-preset and
    # --list-presets have returned above and never ask the network.
    _upd = _update_notice(config)
    if _upd:
        print(_upd[0])
        menu._update_available = (config.config.update_latest_seen, _upd[1])

    _tip = _jxl_thumbnail_tip() if config.config.show_thumbnail_tip else None
    if _tip:
        print(_tip)
        print("     (Hide this tip: 9 - Settings, then 1.)")

    # Main loop
    while True:
        has_last = bool(config.config.last_input_dir)
        choice = menu.show_main_menu(has_last)

        if choice == "0":
            print("Exiting...")
            break

        # The letters of v2.22.0_beta1's menus R and C (show_main_menu maps
        # them too; S stays the script settings).
        choice = {"R": "5", "C": "8"}.get(choice, choice)

        if choice == "1":
            # New job: task, folder, mode, quality, review ([r] runs it).
            menu.new_job_flow(status)

        elif choice == "2" and has_last:
            menu._run_saved_session(config.session_snapshot(), status)

        elif choice == "3":
            menu.presets_menu(status)

        elif choice == "4":
            # Folder list: the task, then a manifest CSV, then the review.
            menu.new_job_flow(status, manifest=True)

        elif choice == "5":
            menu.restamp_flow(status)

        elif choice == "6":
            menu.explorer_thumbnail_flow(status)

        elif choice == "7":
            menu.repair_jbrd_flow(status)

        elif choice == "8":
            menu.clear_icc_cache_flow(status)

        elif choice == "S":
            menu.script_settings_flow()

        elif choice == "U" and getattr(menu, '_update_available', None):
            _url = menu._update_available[1]
            print(f"Release page: {_url}")
            try:
                import webbrowser
                webbrowser.open(_url)
            except Exception:
                pass

        elif choice == "9":
            sub = menu.ui.choose("menu.settings", "Settings",
                                 [("1", "wrapper defaults (workers, staging, marker, distance...)"),
                                  ("2", "script settings (the four scripts' own defaults)"),
                                  ("3", "reset all settings"),
                                  ("4", "move the settings file"), ("0", "back")], "0")
            if sub == "1":
                menu.edit_settings()
            elif sub == "2":
                menu.script_settings_flow()
            elif sub == "3":
                choice = "_reset"
            elif sub == "4":
                choice = "_move"

        if choice == "D":
            status = checker.check_dependencies(force=True)
            menu.display_status(status)

        elif choice == "_reset":
            if RICH_AVAILABLE and console:
                confirm = Confirm.ask(
                    "[red]This will erase all saved settings. Continue?[/red]",
                    default=False
                )
            else:
                confirm_input = input("\nErase all settings? [y/N]: ").strip().lower()
                confirm = confirm_input == 'y'

            if confirm:
                try:
                    if config.config_path.exists():
                        config.config_path.unlink()
                        if RICH_AVAILABLE and console:
                            console.print("[green]✓ Settings erased![/green]")
                        else:
                            print("✓ Settings erased!")

                    config.config = ToolConfig()
                    config.save_config()

                    status = checker.check_dependencies(force=True)
                    menu.display_status(status)
                except Exception as e:
                    menu._print_error(f"Error erasing: {e}")

        elif choice == "_move":
            script_config = SCRIPT_DIR / ".jxl_tools_config.json"
            if platform.system() == "Windows":
                user_profile_dir = Path(os.environ.get("USERPROFILE", Path.home()))
            else:
                user_profile_dir = Path.home()
            user_config = user_profile_dir / ".jxl_tools_config.json"

            if script_config.exists():
                action = "move to User Profile"
                target = user_config
                source = script_config
            elif user_config.exists():
                action = "move to script folder"
                target = script_config
                source = user_config
            else:
                if RICH_AVAILABLE and console:
                    console.print("[yellow]No settings file found.[/yellow]")
                else:
                    print("No settings file found.")
                continue

            if RICH_AVAILABLE and console:
                confirm = Confirm.ask(
                    f"[yellow]Move settings to {action}?[/yellow]",
                    default=True
                )
            else:
                confirm_input = input(f"\nMove settings to {action}? [Y/n]: ").strip().lower()
                confirm = not confirm_input.startswith('n')

            if confirm:
                try:
                    # shutil.move falls back to copy2 when the target exists,
                    # silently overwriting an existing config there — confirm first.
                    if target.exists():
                        if RICH_AVAILABLE and console:
                            ow = Confirm.ask(
                                f"[red]A settings file already exists at {_rich_safe(target.parent)}. Overwrite it?[/red]",
                                default=False
                            )
                        else:
                            ow = input(f"A settings file already exists at {target.parent}. Overwrite? [y/N]: ").strip().lower() == 'y'
                        if not ow:
                            continue
                        target.unlink()
                    shutil.move(str(source), str(target))
                    config.config_path = target
                    if RICH_AVAILABLE and console:
                        console.print(f"[green]✓ Settings moved to {_rich_safe(target.parent)}[/green]")
                    else:
                        print(f"✓ Settings moved to {target.parent}")
                except Exception as e:
                    menu._print_error(f"Error moving: {e}")
            continue


if __name__ == "__main__":
    main()
