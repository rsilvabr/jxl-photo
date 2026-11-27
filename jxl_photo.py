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
TOOLKIT_VERSION = "2.31.1-dev2"
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

# `python jxl_photo.py encode ARGS` = `python jxl_tiff_encoder.py ARGS`: the
# names jxl-photo.exe gives its backends.
_BACKEND_COMMANDS = {
    "encode": "jxl_tiff_encoder.py",
    "decode": "jxl_tiff_decoder.py",
    "recompress": "jxl_recompressor.py",
    "transcode": "jxl_jpeg_transcoder.py",
}


def _run_backend(command: str, argv: List[str]) -> None:
    """Run one backend as if it was started directly: the script next to this
    one, as __main__ (runpy), with `argv` as its arguments, stdout and stderr
    in UTF-8 as the wizard's own start always made them. Called just below,
    before the rest of this file runs, so nothing of the wizard runs and the
    exit code, the output and Ctrl+C are the backend's own."""
    import runpy
    _common._utf8_console()
    script = Path(__file__).parent.resolve() / _BACKEND_COMMANDS[command]
    sys.argv = [str(script)] + list(argv)
    runpy.run_path(str(script), run_name="__main__")


# A backend call is dispatched HERE, before the wizard's imports and
# definitions (rich, the option registry, the screens: ~11 000 lines), which
# it used to run first for nothing (v2.31.0-rc4: 35-40 ms a call; Python
# still compiles this whole file first, ~60 ms, as for any script it runs).
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1].lower() in _BACKEND_COMMANDS:
    _run_backend(sys.argv[1].lower(), sys.argv[2:])
    sys.exit(0)

_G = _common.Script(globals())
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
    from rich.box import SIMPLE as BOX_SIMPLE, ROUNDED as BOX_ROUNDED
    from rich.console import Group
    from rich.prompt import Prompt, Confirm
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


_is_relative_to = _common._is_relative_to


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


# The `mode` a probe parse starts from: tells "no --mode in the flags" from
# every value the flags could give it.
_MODE_NOT_GIVEN = object()


def _child_flags(origin: Optional[str], dest: Optional[str], flags: Optional[str]):
    """The child's OWN parse of the expert flags (its build_parser(), read in
    process like _expert_flags_problem): an argparse Namespace whose `mode` is
    _MODE_NOT_GIVEN unless the flags set it. None when there is nothing to
    parse, the direction is unknown, the child cannot be imported or its
    parser rejects the flags (the gates refuse such a run); the callers then
    read the tokens themselves, which fails closed.

    Abbreviations, spellings and store_true values are the child's to judge:
    six bugs of the tracker were the wrapper's copy of those rules drifting
    from argparse (#210, #263, #457, #518, #603, #604)."""
    tokens = _split_expert_flags(flags or '')
    if not tokens or not origin or not dest:
        return None
    try:
        parser = importlib.import_module(_child_module(origin, dest)).build_parser()
        parser.set_defaults(mode=_MODE_NOT_GIVEN)
    except Exception:
        return None
    try:
        with redirect_stderr(io.StringIO()):
            ns, _extras = parser.parse_known_args(["__jxlphoto_probe__"] + tokens)
    except SystemExit:
        return None
    return ns


def _flags_request_delete(flags: Optional[str], origin: Optional[str] = None,
                          dest: Optional[str] = None) -> bool:
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

    With the direction given, the child's own parse answers (_child_flags).
    Without it, or when the child cannot be imported, the exact spelling
    decides: since v2.30.0 the children refuse an abbreviated flag
    (allow_abbrev=False) and `--delete-source=1` (a store_true flag takes no
    value), so neither can delete anything.
    """
    ns = _child_flags(origin, dest, flags)
    if ns is not None:
        return bool(getattr(ns, "delete_source", False))
    return any(t == '--delete-source' for t in _split_expert_flags(flags or ''))


def _flags_ambiguous_delete(flags: Optional[str]) -> Optional[str]:
    """The first delete-flag token the child's argparse would reject, or None.

    `--delete-source=1` (or a value on --delete-skipped / --delete-confirm-off):
    the delete flags are store_true and take no explicit argument, so argparse
    exits 2 before the child does anything. Caught up front, with a message
    that says why, instead of after the HHMM token for a doomed run. (Until
    v2.30.0 this also caught `--delete-s`, ambiguous between --delete-source
    and --delete-skipped; the children now refuse every abbreviation, and the
    expert-flags parse names it.)"""
    families = ('--delete-source', '--delete-skipped', '--delete-confirm-off')
    for t in _split_expert_flags(flags or ''):
        if '=' in t and t.split('=', 1)[0] in families:
            return t
    return None


def _flags_set_mode(flags: Optional[str], origin: Optional[str] = None,
                    dest: Optional[str] = None) -> Optional[str]:
    """The expert-flag token that would set the child's --mode, or None.

    The wrapper decides everything around a run from ITS mode: the in-place
    and delete gates, the collision scan, the counts in the confirmation. A
    `--mode 8` in the expert flags wins in the child's argparse (it comes
    last), so the run would replace files in place behind a gate that saw
    another mode (W-B of the 2026-11-03 audit).

    With the direction given, the child's own parse decides (_child_flags);
    the token only names it in the message ("--mode" when none spells it).
    Without it, the exact spelling: since v2.30.0 every child refuses `--mo`
    and `--mod` (allow_abbrev=False)."""
    token = next((t for t in _split_expert_flags(flags or '')
                  if t.split('=', 1)[0] == '--mode'), None)
    ns = _child_flags(origin, dest, flags)
    if ns is None:
        return token
    if getattr(ns, "mode", _MODE_NOT_GIVEN) is _MODE_NOT_GIVEN:
        return None
    return token or "--mode"


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
    if getattr(_ns, "serve", False):
        return ("--serve is for a program that sends the script its files one by one "
                "(photo-backup), not for a run of the wizard")
    return None


def _strip_surrounding_quotes(value: str) -> str:
    """Strip balanced surrounding quotes from pasted paths
    (e.g. Explorer's 'Copy as path' wraps the path in double quotes)."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _same_folder(a, b) -> bool:
    """Do two typed folder paths name the same folder? A trailing separator,
    the case of the letters and '.'/'..' parts do not count (rc6, D11: a
    folder typed with a '\\' at the end was another folder to a text
    comparison). Nothing is read from the disk."""
    if not a or not b:
        return False
    try:
        return (os.path.normcase(os.path.abspath(str(a)))
                == os.path.normcase(os.path.abspath(str(b))))
    except (TypeError, ValueError):
        return False


# cjxl's own ceiling ("Allowed range: 0.0 ... 25.0" in 0.11.x, "range: 0.0 ..
# 25.0" in 0.12.0; d=26 is refused). The toolkit kept 15 until v2.22.4.
_MAX_DISTANCE = 25.0


def _sane_distance(value: Any, fallback: float = 0.1) -> float:
    """Clamp any stored/typed distance into cjxl's valid 0.0-25.0 range (libjxl 0.11 and 0.12).

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
    return max(0.0, min(result, _MAX_DISTANCE))


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

    for key, label, low, high in _SESSION_NUMBERS:
        raw = session.get(key)
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

    for key, allowed in _SESSION_CHOICES.items():
        raw = session.get(key)
        if raw in (None, ""):
            continue
        if not isinstance(raw, str) or raw.lower() not in allowed:
            return (f"{key[len('last_'):]} is not a known value: {raw!r} "
                    f"(valid: {'/'.join(allowed)})")

    # The two fields that pick WHICH script runs. Case-sensitive on purpose
    # (see the constants): accepting "Tiff" here would route the replay to
    # the transcoder on a TIFF tree while the panel says TIFF -> JXL.
    for key, allowed in (("last_origin_format", _SESSION_ORIGIN_FORMATS),
                           ("last_dest_format", _SESSION_DEST_FORMATS)):
        raw = session.get(key)
        if raw in (None, ""):
            continue
        if not isinstance(raw, str) or raw not in allowed:
            return (f"{key[len('last_'):]} is not a known value: {raw!r} "
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


def _session_int(session: Dict, field: str, fallback: Optional[int]) -> Optional[int]:
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


_replace_suffix_token = _common._replace_suffix_token


_marker_matches = _common._marker_matches


_path_excluded_below_root = _common._path_excluded_below_root


def _append_expert_flags(cmd: List[str], flags: str, origin: Optional[str] = None,
                         dest: Optional[str] = None) -> None:
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
    if _flags_request_delete(flags, origin, dest) and not any(
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
    # JXL -> JPEG/PNG rows pass it as the transcoder's --export-jpeg-folder.
    "exportjxlfolder": frozenset({"tiff2jxl", "jxl2jxl", "jxl2jpeg", "jxl2png"}),
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
_COLLAPSING_MODES = _common._COLLAPSING_MODES


def _recompress_entry_in_place(source, dest_path, mode, mode_config=None,
                               flags=None) -> bool:
    """Does a JXL -> JXL (recompressor) run REPLACE its sources?

    Mode 8 always does (the output is the source path). Mode 0 does when it
    has no output folder — or when that folder IS the source folder, which is
    what every manifest row sends: the loader fills an empty Destination cell
    with the Source, so testing "no Destination" alone never matched a
    manifest entry, and the in-place gate was silently skipped. Modes 3-7
    ask the recompressor's own plan (_recompressor_plan_maps_onto_itself,
    rc8: the same answer as _jxl_run_replaces_in_place — the review of rc8's
    fixes found the two disagreeing).
    """
    if mode == 8:
        return True
    if mode == 2:
        return _recompressor_replaces_file(source, dest_path, mode)
    if mode in (3, 4, 5, 6, 7):
        return _recompressor_plan_maps_onto_itself(source, mode, mode_config, flags)
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


def _recompressor_replaces_file(source, dest_path, mode) -> bool:
    """Mode 2 writes flat into the Destination: does the recompressor's OWN
    plan (_planned_output) put a FILE Source's output on that file (#708: a
    row with its folder as Destination replaced it in place — the child's
    in-place path — without the wrapper's confirmation)? A folder Source:
    _folder_row_replaces_files. Cannot tell: True, the confirmation is asked
    (fail closed)."""
    try:
        src = Path(source)
        if src.is_dir():
            return _folder_row_replaces_files(src, dest_path, mode)
        if not src.is_file():
            return False
        rec = importlib.import_module('jxl_recompressor')
        argv = [str(src)] + ([str(dest_path)] if dest_path else []) + ['--mode', str(mode)]
        with redirect_stderr(io.StringIO()):
            ns = rec.build_parser().parse_args(argv)
        out = rec._planned_output(src, ns)
    except SystemExit:
        return True
    except Exception:
        return True
    if out is None:
        return False
    try:
        if os.path.samefile(str(out), str(src)):
            return True
    except OSError:
        pass
    return (os.path.normcase(os.path.abspath(str(out)))
            == os.path.normcase(os.path.abspath(str(src))))


def _jxl_run_replaces_in_place(origin, dest, mode, input_dir, output_dir,
                               mode_config=None, flags=None) -> bool:
    """Does this JXL -> JXL run (a direct run, a repeat, a preset) replace its
    sources in place? One answer for the wizard's in-place confirmation, the
    unattended refusal and the child's --delete-confirm-off
    (_policy_delete): modes 0/8, mode 2 when its output folder sits inside
    the input folder and already holds JXLs (_recompress_entry_in_place; the
    review of rc6, M1: the flag was sent without the confirmation), and the
    modes whose own folders the run is pointed at (rc8, Fable's S6:
    _recompressor_plan_maps_onto_itself)."""
    if origin != 'jxl' or dest != 'jxl':
        return False
    if mode in (0, 8):
        return True
    if mode == 2 and input_dir and output_dir:
        return _recompress_entry_in_place(input_dir, output_dir, 2)
    if mode in (3, 4, 5, 6, 7) and input_dir:
        return _recompressor_plan_maps_onto_itself(input_dir, mode, mode_config, flags)
    return False


def _mode_config_with_marker(mode_config, config_marker):
    """The run's mode_config with the marker the wizard sends its child — the
    run's own, else the wizard's setting (the export_marker Opt): what a plan
    probe must anchor on."""
    mc = dict(mode_config or {})
    if not mc.get('export_marker') and config_marker:
        mc['export_marker'] = config_marker
    return mc


def _menu_marker(menu):
    """The wizard's own export marker setting, or None."""
    return getattr(getattr(getattr(menu, 'config', None), 'config', None),
                   'export_marker', None)


def _recompressor_plan_maps_onto_itself(input_dir, mode, mode_config=None,
                                        flags=None) -> bool:
    """Does the recompressor's own plan (_planned_output) put a JXL of this
    folder back on itself (rc8, Fable's S6)? Mode 5 run AT a folder named
    JXL_FOLDER_NAME and mode 6 at <marker>/EXPORT_JXL_FOLDER do: the child
    then asked its own in-place question, and an unattended preset ended
    with exit 3. A probe name in the folder is enough — the plan is path
    arithmetic, no file is read —, under the run's marker, subfolder and
    output folder (mode_config; a missing one leaves the child's setting) and
    its expert flags, which come last and win (#664): the child's own parse,
    read by _child_plan_globals. A FILE input: _recompressor_replaces_file.
    Cannot tell: True."""
    try:
        src = Path(input_dir)
        if src.is_file():
            return _recompressor_replaces_file(src, None, mode)
        if not src.is_dir():
            return False
        mc = mode_config or {}
        rec = importlib.import_module('jxl_recompressor')
        probe = src / "__jxlphoto_probe__.jxl"
        argv = [str(src), '--mode', str(mode)]
        for key, flag in (('export_marker', '--export-marker'),
                          ('export_subfolder', '--export-subfolder'),
                          ('export_jxl_folder', '--export-jxl-folder')):
            if mc.get(key):
                argv += [flag, str(mc[key])]
        argv += _split_expert_flags(flags or '')
        with redirect_stderr(io.StringIO()):
            ns = rec.build_parser().parse_args(argv)
        with redirect_stderr(io.StringIO()), _with_child_marker(
                rec, *_child_plan_globals(ns, "jxl2jxl")):
            out = rec._planned_output(probe, ns)
    except SystemExit:
        return True
    except Exception:
        return True
    if out is None:
        return False
    return (os.path.normcase(os.path.abspath(str(out)))
            == os.path.normcase(os.path.abspath(str(probe))))


def _folder_row_replaces_files(src: Path, dest_path, mode) -> bool:
    """A FOLDER Source in mode 2 whose Destination is a folder INSIDE it: the
    recompressor's flat, recursive run finds the JXLs already in that folder
    and puts each one back on itself — the in-place path — and its own HHMM
    question came up in the middle of the manifest (with no stdin: exit 3,
    "Cancelled", and the scheduled run said it went well: rc6, Fable's second
    sweep D13; #708 had closed the FILE Source). Asked of the recompressor's
    own plan (_planned_output) for the files directly in the Destination —
    a deeper one is flattened into it, never onto itself — and none when the
    Destination's path below the Source names one of its own output folders
    (its finder leaves those out). The Destination equal to the Source:
    False (the recompressor refuses it, exit 2). Cannot tell: True."""
    if not dest_path:
        return False
    try:
        dst = Path(dest_path)
        # Real paths: a Destination reached through a junction or an 8.3
        # short name is inside the Source too, as the recompressor's own
        # same-folder test (samefile) sees it (rc6's second review, N5).
        s_real = os.path.realpath(str(src))
        d_real = os.path.realpath(str(dst))
        s_key, d_key = os.path.normcase(s_real), os.path.normcase(d_real)
        if d_key == s_key or not d_key.startswith(s_key.rstrip(os.sep) + os.sep):
            return False
        if not dst.is_dir():
            return False
        rec = importlib.import_module('jxl_recompressor')
        below = [p.lower() for p in Path(os.path.relpath(d_real, s_real)).parts]
        if rec._is_own_output_path(below):
            return False
        with redirect_stderr(io.StringIO()):
            ns = rec.build_parser().parse_args([str(src), str(dst), '--mode', str(mode)])
        for f in dst.iterdir():
            if not (f.suffix.lower() == '.jxl' and f.is_file()):
                continue
            out = rec._planned_output(f, ns)
            if out is not None and (os.path.normcase(os.path.abspath(str(out)))
                                    == os.path.normcase(os.path.abspath(str(f)))):
                return True
        return False
    except SystemExit:
        return True
    except Exception:
        return True


_run_collapses_structure = _common._run_collapses_structure


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
        if jxl_folder and hasattr(child, 'EXPORT_JPEG_FOLDER'):
            # The transcoder: the name reaches it only on a JXL -> JPEG/PNG
            # run (--export-jpeg-folder), whose outputs go there.
            saved['EXPORT_JPEG_FOLDER'] = child.EXPORT_JPEG_FOLDER
            child.EXPORT_JPEG_FOLDER = jxl_folder
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
# and never prints it. One spelling for both sides, in jxl_photo_common.py.
CHILD_SUMMARY_PREFIX = _common.SUMMARY_PREFIX

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
    thumb_latest_seen: Optional[str] = None      # jxl-thumb's latest release tag, same ask

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
    # What a NEW job of each task starts from ({task id: {last_*: value}},
    # _task_memory_entry): the last run of THAT task (#704-#709). The last_*
    # fields above stay the last job of any task — what "Repeat last job"
    # replays and older copies read. Not a last_* name, so no preset and no
    # session snapshot carries it; an older copy drops it when it saves.
    task_memory: Dict[str, Dict] = field(default_factory=dict)

    dependencies_checked: bool = False
    available_features: Dict[str, bool] = field(default_factory=dict)


class ConfigManager:
    """Manages persistent configuration for jxl_tools"""

    def __init__(self):
        import threading
        self.config_path = self._get_config_path()
        self.config = ToolConfig()
        # The background release lookup writes its answer from its own thread
        # (save_update_answer); every write of the file holds this lock.
        self._save_lock = threading.RLock()
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
        with self._save_lock:
            self._write_config(asdict(self.config))

    def save_update_answer(self, checked_on: str, tag: Optional[str],
                           thumb: Optional[str]) -> None:
        """Keep a release lookup's answer, from any thread. Sets the fields in
        memory, then rewrites ONLY those keys in the file on disk (read, change,
        atomic write, under the save lock): the background thread must never
        serialise the whole config while the menu thread changes it, and the
        answer has to reach the file even when the menu is never drawn again —
        a run usually ends by closing the window, so the NEXT run shows it.
        A file that exists but does not parse is left alone."""
        with self._save_lock:
            cfg = self.config
            cfg.update_checked_on = checked_on
            if tag:
                cfg.update_latest_seen = tag
            if thumb:
                cfg.thumb_latest_seen = thumb
            data: Dict[str, Any] = {}
            if self.config_path.exists():
                try:
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    return
                if not isinstance(data, dict):
                    return
            data["update_checked_on"] = cfg.update_checked_on
            data["update_latest_seen"] = cfg.update_latest_seen
            data["thumb_latest_seen"] = cfg.thumb_latest_seen
            self._write_config(data)

    def _write_config(self, data: Dict[str, Any]) -> None:
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
                    json.dump(data, f, indent=2, ensure_ascii=False)
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
        # These probes import a module only to see that it is installed
        # (and so that PyInstaller bundles it into the exe).
        try:
            import tifffile  # noqa: F401
            import numpy  # noqa: F401
            return True
        except ImportError:
            return False

    def _check_imagecodecs(self) -> bool:
        """Check if imagecodecs is available for compressed TIFF support."""
        try:
            import imagecodecs  # noqa: F401
            return True
        except ImportError:
            return False


class DependencyChecker:
    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager
        self._deps_status = None  # cached result; force=True bypasses

    def _check_pillow(self) -> bool:
        try:
            import PIL  # noqa: F401
            return True
        except ImportError:
            return False

    def _check_rich(self) -> bool:
        try:
            import rich  # noqa: F401
            import rich.console  # noqa: F401
            import rich.table  # noqa: F401
            import rich.panel  # noqa: F401
            import rich.prompt  # noqa: F401
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
            # Ships beside cjxl/djxl; the backends ask it whether a JXL came
            # from a rotated JPEG or holds float/animated content (#633).
            # `-h` is its only option that exits 0 without a file.
            'jxlinfo': ['jxlinfo', '-h'],
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
            # stdin NUL, as the scripts run every tool: an exiftool(-k)
            # waited for a key until the timeout and read as missing.
            result = subprocess.run([path] + args, capture_output=True,
                                  stdin=subprocess.DEVNULL,
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
        elif status.get('jxlinfo') is False:
            # `is False`: a status saved by an older version has no entry.
            parts[0] = ("[⚠] cjxl/djxl (jxlinfo missing: float/animated JXLs "
                        "are not recognised)")
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
        mode_names = {m: _mode_title(m, self.export_marker) for m in range(9)}
        mode_names[1] += f" ({_sub1})"
        mode_names[3] += f" ({_sub3})"

        lines.append(f"\n{'='*60}")
        if analysis['recommended_mode'] is not None:
            rec_mode = analysis['recommended_mode']
            rec_name = mode_names.get(rec_mode, f"Mode {rec_mode}")
            confidence = analysis['confidence']
            confidence_icon = {"high": "✓✓", "medium": "✓", "low": "?"}[confidence]

            lines.append(f"{confidence_icon} RECOMMENDED: Mode {rec_mode} — {rec_name}")
            lines.append(f"   Confidence: {confidence}")

            if analysis['reasoning']:
                lines.append("   Reasoning:")
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
                or _flags_request_delete(workflow.get('expert_flags'),
                                         workflow.get('origin_format'),
                                         workflow.get('dest_format')))


_NO_DERIVATIVE_WITH_DELETE = ("A derivative (colour conversion, resize, sharpening) never "
                              "deletes its source, so it is not offered while [D] "
                              "(delete originals) is on.")


def _has_derivative_options(advanced: Dict) -> bool:
    """True when the options produce a DERIVATIVE: colour conversion, resize
    or sharpening. These never delete, never run in place and carry a
    jxlphoto-derived recipe marker."""
    return bool(advanced.get('output_icc') or advanced.get('resize_mode')
                or (advanced.get('sharpen') or 'none') != 'none')


# --- A delete or a derivative typed in the expert flags (rc6, D15) -----------
# The checks before the HHMM token read the run's own options; the expert
# flags reach the child verbatim, so a --delete-source, a --resize-long or an
# --output-icc typed there was seen by no check, and the child refused the run
# with exit 2 AFTER the token. The owner's call: such a delete counts like [D].

def _flags_shape_derivative(flags: Optional[str], origin: Optional[str],
                            dest: Optional[str], colour: bool = True) -> bool:
    """Do the expert flags make the child write a DERIVATIVE (a resize or a
    sharpening; with `colour`, a colour conversion too)? The child's own parse
    answers. `colour=False` for the transcoder's bit-exact route
    (--force-transcode), which ignores a profile and keeps its delete."""
    ns = _child_flags(origin, dest, flags)
    if ns is None:
        return False
    shaped = bool(getattr(ns, 'resize_long', None) or getattr(ns, 'resize_short', None)
                  or getattr(ns, 'resize_percent', None)
                  or (getattr(ns, 'sharpen', None) or 'none') != 'none')
    coloured = bool(getattr(ns, 'output_icc', None) or getattr(ns, 'icc_profile', None)
                    or getattr(ns, 'to_srgb', False))
    return shaped or (colour and coloured)


# The conversions of the transcoder's bit-exact route (--force-transcode).
_BIT_EXACT_CONVERSIONS = ('transcode_lossless', 'jxl_to_jpeg_lossless')


def _flags_derivative_refused(workflow: Dict) -> bool:
    """Would the child refuse this run's delete for a derivative typed in its
    expert flags (rc6, D15; the review's S1)? The scripts' own rules: resize
    or sharpening never with a delete (recompressor and transcoder, every
    route), a colour conversion never with one except on the transcoder's
    bit-exact route (the profile is ignored there, N3). The TIFF scripts
    have no such flag."""
    origin, dest = workflow.get('origin_format'), workflow.get('dest_format')
    if (origin, dest) in (('tiff', 'jxl'), ('jxl', 'tiff')) or not origin or not dest:
        return False
    return _flags_shape_derivative(
        workflow.get('expert_flags'), origin, dest,
        colour=workflow.get('conversion_type') not in _BIT_EXACT_CONVERSIONS)


def _flags_turn_guard_off(flags: Optional[str], origin: Optional[str],
                          dest: Optional[str]) -> bool:
    """Is --no-overwrite-guard among the expert flags?"""
    ns = _child_flags(origin, dest, flags)
    if ns is not None:
        return bool(getattr(ns, 'no_overwrite_guard', False))
    return '--no-overwrite-guard' in _split_expert_flags(flags or '')


def _job_deletes(workflow: Dict) -> bool:
    """Does this run delete its sources — [D] or --delete-source in the
    expert flags (rc6, D15: both count the same before the HHMM)?"""
    return bool((workflow.get('advanced_options') or {}).get('delete_source')
                or _flags_request_delete(workflow.get('expert_flags'),
                                         workflow.get('origin_format'),
                                         workflow.get('dest_format')))


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
    if conv not in ('jxl_to_jpeg_force', 'jxl_to_png'):
        return None
    # The format and the bit depth the child will use: the expert flags come
    # last in its argv and win (rc8, Fable's S3: a --bit-depth 8 or a --format
    # jpeg typed there reached a child that refused the delete with exit 2,
    # after the HHMM token). Its own parse answers, as for a delete (#725).
    fmt = 'jpeg' if conv == 'jxl_to_jpeg_force' else 'png'
    depth = str(_bit_depth_or_default(workflow, 'png'))
    ns = _child_flags('jxl', workflow.get('dest_format'), workflow.get('expert_flags'))
    if ns is not None:
        if getattr(ns, 'format', None):
            fmt = 'png' if ns.format == 'png' else 'jpeg'
        if getattr(ns, 'bit_depth', None):
            depth = str(ns.bit_depth)
    if fmt == 'jpeg':
        return ("a JPEG decoded from a JXL is lossy, not a copy of it — only the "
                "bit-exact recovery of a JPEG archive deletes its JXL")
    if depth != '16':
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
    Source itself (the builder then sends no output, or the same folder), or
    mode 2 whose Destination inside the Source already holds JXLs (rc6's
    second review, N1). --output-icc and --rename-from refuse those (exit 2
    per row)."""
    rows = []
    for src, dst, mode in entries:
        if mode == 8:
            rows.append((src, dst, mode))
        elif mode == 2:
            if _recompress_entry_in_place(src, dst, 2):
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


def _export_folder_settable(origin: str, dest: str) -> bool:
    """The directions whose child takes the modes 6/7 output folder name:
    --export-jxl-folder (encoder, recompressor), --export-jpeg-folder
    (transcoder, JXL -> JPEG/PNG)."""
    return origin in ('tiff', 'jxl') and dest == 'jxl' or (origin == 'jxl' and dest in ('jpeg', 'png'))


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

    `override` is the run's `--export-jxl-folder` / `--export-jpeg-folder` (the
    wrapper's `mode_config['export_jxl_folder']`): it only means something for
    the directions whose child accepts one (TIFF -> JXL, JXL -> JXL, and JXL ->
    JPEG/PNG since v2.23.0).
    """
    if override and _export_folder_settable(origin, dest):
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


# After a second Ctrl+C the backend stops at once by itself (it kills its
# tools and removes its temps): that long before the wizard kills the tree.
_CHILD_STOP_GRACE = 15


def _kill_process_tree(process) -> None:
    """Kill a child script AND what it started (W3 of the 2026-10-08 audit).

    On Windows Popen.kill() is TerminateProcess on the Python child alone: the
    cjxl/djxl/exiftool it launched kept running and left their
    <uuid>_name.tmp files beside the finals, where no sweep looks. taskkill /T
    takes the whole tree. Only a real Popen gets it: a pid of a stand-in
    object could name an unrelated process. taskkill runs in a process group
    of its own: a Ctrl+C pressed meanwhile (the user pressing again) must not
    kill it before it took the tree, which would leave Popen.kill() alone."""
    if os.name == "nt" and isinstance(process, subprocess.Popen):
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
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


# The four child scripts and the common module whose TOOLKIT_VERSION must
# match the wrapper's.
_VERSIONED_CHILDREN = ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
                       "jxl_jpeg_transcoder.py", "jxl_recompressor.py",
                       "jxl_photo_common.py")
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
        'encode_tag': ('ENCODE_TAG_MODE', 'history'),
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


def _child_eight_bit():
    """The encoder's EIGHT_BIT_DISTANCE as the wizard offers it ('auto',
    'same' or a number) and its EIGHT_BIT_LOSSLESS_UP_TO. The shipped
    defaults only when the encoder cannot be imported."""
    try:
        mod = importlib.import_module('jxl_tiff_encoder')
    except Exception:
        return "auto", 0.1
    try:
        value = mod._parse_eight_bit_distance(getattr(mod, 'EIGHT_BIT_DISTANCE', "auto"))
        up_to = float(getattr(mod, 'EIGHT_BIT_LOSSLESS_UP_TO'))
    except Exception:
        return "auto", 0.1
    return (value if isinstance(value, str) else f"{value:g}"), up_to


def _valid_eight_bit_distance(text: str):
    """An --eight-bit-distance value the encoder accepts, normalised, or None."""
    t = str(text or "").strip().lower()
    if t in ("auto", "same"):
        return t
    try:
        d = float(t)
    except ValueError:
        return None
    return f"{d:g}" if 0 <= d <= _MAX_DISTANCE else None


def _eight_bit_shown(value, up_to) -> str:
    if value == "auto":
        return f"lossless up to d={up_to:g}, then the Distance"
    if value == "same":
        return "same as Distance"
    return _distance_shown(value)


def _eight_bit_effective(value, up_to, distance):
    """The distance an 8-bit TIFF (not a scan) gets in a run at `distance`."""
    if value == "same":
        return float(distance)
    if value == "auto":
        return 0.0 if float(distance) <= float(up_to) else float(distance)
    return float(value)


def _mode_title(mode, marker: str = "_EXPORT") -> str:
    """The one name of each mode, on every screen (the mode table, the review,
    Auto Mode, the [D] layouts, the detailed view): there were five lists of
    names, and mode 6 was "Marker _EXPORT (full)", "Marker export (full)",
    "Export (full)" and "below the export marker" depending on the screen."""
    try:
        mode = int(mode)
    except (TypeError, ValueError):
        return "?"
    return {
        0: "In-place",
        1: "Subfolder",
        2: "Flat → one output folder",
        3: "Recursive subfolders",
        4: "Folder rename (suffix swap)",
        5: "Sibling folder",
        6: f"Marker {marker} (full)",
        7: f"Marker {marker} (one subfolder)",
        8: "In-place recursive",
        99: "Manifest (each row its own mode)",
    }.get(mode, f"Mode {mode}")


def _setting_shown(value) -> str:
    """A script setting as a person reads it: no JSON quotes, none for null,
    a list as comma-separated words."""
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or "(empty list)"
    if value == "":
        return "(empty)"
    return str(value)


def _option_shown(text) -> str:
    """The options screen's values in the review's words."""
    return {"True": "yes", "False": "no", "None": "—"}.get(str(text), str(text))


def _task_label(origin, dest, conv=None) -> str:
    """The New job task a stored run belongs to ("Archive TIFFs as JXL
    masters"), else its direction ("TIFF → JXL")."""
    for _tid, label, o, d, convs in TASKS:
        if o == origin and d == dest and (not conv or conv in convs):
            return label
    for _tid, label, o, d, _convs in TASKS:
        if o == origin and d == dest:
            return label
    return f"{(origin or '?').upper()} → {(dest or '?').upper()}"


# The distances the "Archive TIFFs" quality step offers, with their names.
# 0.05 is the floor of cjxl 0.12: every lossy distance below it gives the
# same file (the encoder's _MIN_EFFECTIVE_DISTANCE).
_DISTANCE_CHOICES = [
    (0.0, "lossless", ""),
    (0.05, "finest lossy available", ""),
    (0.1, "near-lossless", "recommended"),
    (0.5, "high-quality lossy", "headroom for later edits"),
    (0.7, "visually lossless", "keeps the grain texture"),
    (1.0, "visually lossless", "JPEG XL's definition"),
]
# "Make smaller or resized JXL copies": copies of masters, so cjxl's own
# recommended range (0.5 .. 3.0, `cjxl --help`), plus d=5 for screen-only
# copies. Nothing above 5: from d=10 libjxl halves the resolution itself
# (enc_frame.cc), so quality drops a step there for little saving; a smaller
# copy is better made with a resize (sweep of 2026-11-06, 8 photos).
# "quality" is cjxl's -q scale (distance = 0.1 + (100 - q) * 0.09).
_RECOMPRESS_CHOICES = [
    (0.5, "high-quality lossy", "headroom for later edits"),
    (0.7, "visually lossless", "keeps the grain texture"),
    (1.0, "visually lossless", "JPEG XL's definition"),
    (2.0, "about quality 79", "phones, sharing"),
    (3.0, "about quality 68", "web and social media"),
    (5.0, "about quality 46", "screens only"),
]
# Typical file sizes shown next to the distances (MB at 45 / 24 / 12 MP):
# medians of 20 Z7+Z8, 10 Zf and 10 D700 photos (Capture One exports,
# ProPhoto, cropped ones scaled to the full sensor), cjxl 0.12.0 effort 7,
# no Explorer thumbnail box; measured 2026-11-06 (docs/file_sizes.md).
# From d=0.2 on a 16-bit and an 8-bit TIFF give the same size; lossless and
# d=0.05 differ, so those have an 8-bit row of their own.
_SIZE_COLUMNS = ["45 MP", "24 MP", "12 MP"]
_TYPICAL_SIZE_MB = {
    0.0: (172.9, 89.0, 43.1), 0.05: (42.3, 20.6, 9.9), 0.1: (31.0, 14.8, 7.2),
    0.2: (21.2, 10.1, 4.8), 0.3: (16.0, 7.6, 3.5), 0.5: (10.7, 4.9, 2.4),
    0.7: (7.8, 3.7, 1.7), 1.0: (5.5, 2.7, 1.2), 2.0: (2.8, 1.4, 0.57),
    3.0: (1.75, 0.94, 0.37), 5.0: (1.11, 0.64, 0.26), 10.0: (0.56, 0.41, 0.15),
    25.0: (0.29, 0.16, 0.08),
}
_TYPICAL_TIFF_MB = [
    ("TIFF 16-bit, uncompressed", (272.7, 146.3, 72.3)),
    ("TIFF 16-bit, ZIP", (227.47, 119.6, 59.9)),
    ("TIFF 8-bit, uncompressed", (136.3, 73.2, 36.2)),
    ("TIFF 8-bit, ZIP", (61.3, 30.1, 16.0)),
]
_TYPICAL_8BIT_MB = {0.0: (43.4, 20.9, 10.2), 0.05: (46.7, 23.0, 11.1)}


def _size_text(mb: float) -> str:
    """12 MB, 5.5 MB, 290 KB: two figures are all a median of ten photos holds."""
    if mb >= 10:
        return f"{mb:.0f} MB"
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{mb * 1000:.0f} KB"


def _choice_label(v, name, note, width: int = 0) -> str:
    """"d=0.1  — near-lossless (recommended)": the distance padded to `width`,
    so the dashes of one list line up."""
    return f"d={v:g}".ljust(width) + f" — {name}" + (f" ({note})" if note else "")


def _label_width(choices) -> int:
    return max(len(f"d={v:g}") for v, _n, _x in choices)


def _size_cells(distance) -> Tuple[str, ...]:
    """The typical sizes of `distance` ("" for one that was not measured)."""
    try:
        sizes = _TYPICAL_SIZE_MB.get(float(distance))
    except (TypeError, ValueError):
        sizes = None
    # right-aligned, so the plain renderer lines the numbers up too
    return (tuple(_size_text(s).rjust(6) for s in sizes) if sizes
            else ("",) * len(_SIZE_COLUMNS))


_SIZE_TITLE = "JXL size from a 16-bit TIFF (typical)"
_SIZE_TITLE_COPIES = "JXL size of a full-size copy (typical)"
_SIZE_NOTE = "A photo can be half or twice these, depending on its detail and noise."
_SIZE_NOTE_COPIES = (f"{_SIZE_NOTE} A resized copy (Resize, in the options) is smaller.")
_SIZE_NOTE_8BIT = ("From an 8-bit TIFF: lossless is 43 / 21 / 10 MB (about the size of "
                   "d=0.05), lossy about the same as above.")

_DISTANCE_PROMPT = ("Distance (0 = lossless, 0.05 = finest lossy, 0.1 = near-lossless, "
                    "0.7-1 = visually lossless)")


def _distance_label(value) -> str:
    """The name of a distance ("" for one in between), as the review shows it."""
    try:
        d = float(value)
    except (TypeError, ValueError):
        return ""
    if d == 0:
        return "lossless"
    if d <= 0.05:
        return "finest lossy"
    if d <= 0.1:
        return "near-lossless"
    choices = _DISTANCE_CHOICES + _RECOMPRESS_CHOICES
    for v, name, note in choices:
        if d == v:
            # two entries share "visually lossless": the note tells them apart
            if note and any(n == name and x != v for x, n, _y in choices):
                return f"{name} ({note})"
            return name
    return ""


def _distance_shown(value) -> str:
    try:
        text = f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)
    label = _distance_label(value)
    return f"{text} — {label}" if label else text


# The review lists what the user chooses first, then where it runs, then the
# technical switches (v2.23.0). Keys not named keep their place after these.
_REVIEW_ROW_ORDER = (
    "conversion", "distance", "scan_distance", "eight_bit_distance", "quality", "effort",
    "derivative",
    "output_icc", "icc_profile", "shaping", "bit_depth", "compression", "add_preview",
    "decode_mode", "existing", "on_downgrade", "on_regeneration", "on_scan",
    "delete", "workers", "staging", "use_ram",
    "overwrite_guard", "d50_patch")


def _review_order(rows):
    rank = {k: i for i, k in enumerate(_REVIEW_ROW_ORDER)}
    return [r for _i, r in sorted(enumerate(rows),
                                  key=lambda t: (rank.get(t[1][0], len(rank)), t[0]))]


def _valid_scan_distance(text: str):
    """A --scan-distance value the encoder accepts, normalised, or None."""
    t = str(text or "").strip().lower()
    if t == "same":
        return "same"
    try:
        d = float(t)
    except ValueError:
        return None
    return f"{d:g}" if 0 <= d <= _MAX_DISTANCE else None


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


_WIZARD_POLICIES = ("copy", "skip", "convert")


def _wizard_policy(setting: str, wizard_default: str) -> str:
    """The recompressor's own policy (ON_DOWNGRADE, ...), which the wizard
    always sends; `wizard_default` only when the script says 'ask' — on the
    wrapper's pipe an 'ask' is a silent skip (#347) — or cannot be read."""
    value = _child_setting('jxl_recompressor', setting, wizard_default)
    return value if value in _WIZARD_POLICIES else wizard_default


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


def _is_decode_lossy(origin: str, dest: str, conv_type: Optional[str]) -> bool:
    """The transcoder's decode conversions that shape pixels (resize,
    sharpening, rename apply); the lossless recovery cannot."""
    return (origin == 'jxl' and dest in ('jpeg', 'png')
            and conv_type in _DECODE_LOSSY_CONVERSIONS)


def _rename_reaches_child(direction: str, decode_lossy: bool, manifest: bool) -> bool:
    """Does build_child_cmd pass --rename-from/--rename-to? The recompressor
    plans the rename up front; the transcoder applies it on its lossy decode
    only (the bit-exact recovery keeps the original name), and only the
    manifest route passes it there. Every other child never gets it."""
    return direction == "jxl2jxl" or (manifest and decode_lossy)


# The transcoder's flags per conversion type: build_child_cmd
# (_policy_conversion) and the plan the wrapper asks the transcoder about
# (_child_plan_args) read this one table. --from-jpeg keeps a lossy
# JPEG -> JXL run off the PNGs in the folder (in mode 8 it would delete them);
# --from-jxl keeps the auto JXL -> JPEG run from transcoding the folder's JPEGs
# INTO JXL; the PNG output never transcodes even when a jbrd box is present.
_TRANSCODER_CONVERSION_FLAGS = {
    'transcode_lossless': ('--force-transcode',),
    'convert_lossy': ('--force-convert', '--from-jpeg'),
    'jxl_to_jpeg_auto': ('--from-jxl',),
    'jxl_to_jpeg_lossless': ('--force-transcode', '--decode'),
    'jxl_to_jpeg_force': ('--force-convert', '--decode'),
    'jxl_to_png': ('--force-convert', '--decode'),
}


def _transcoder_format_flags(dest: str) -> List[str]:
    """The transcoder's output format, from the destination."""
    if dest == 'png':
        return ['--format', 'png']
    if dest in ('jpeg', 'jpg'):
        return ['--format', 'jpeg']
    return []


class _CmdCtx:
    """What build_child_cmd's rules look at, computed once per command."""

    def __init__(self, origin, dest, source, dest_path, mode, workers,
                 workflow, advanced, manifest, config_marker, quiet=False):
        self.quiet = quiet          # the plan's argv: build it, say nothing
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
        self.decode_lossy = _is_decode_lossy(origin, dest, self.conv_type)

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
    Opt("export_jpeg_folder", "--export-jpeg-folder", _D({"jxl2jpeg", "jxl2png"}), "value",
        "files", "advanced", "Modes 6/7 output folder",
        "Name of the JPEG/PNG folder modes 6/7 write next to the marker.",
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
        "Encode record", "The cjxl d=/e= record is always in xmpMM:History; a visible copy: "
        "history = none, comment = Explorer's Comments, title = the Title, software = EXIF "
        "Software; xmp = history; off = no record.",
        choices=('history', 'comment', 'title', 'xmp', 'software', 'off'),
        legacy_key="advanced.encode_tag"),
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
    Opt("eight_bit_distance", "--eight-bit-distance", _TIFF2JXL, "value", "colour", "advanced",
        "8-bit TIFF distance", "Distance for 8-bit TIFFs that are not film scans ('auto' = "
        "lossless at low distances, 'same' = the run's).",
        legacy_key="advanced.eight_bit_distance"),
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
        "advanced", "Encode record", "The recompressor's cjxl d=/e= record is always in "
        "xmpMM:History; a visible copy: history = none, comment, title, software; xmp = "
        "history; off = no record.", choices=('history', 'comment', 'title', 'xmp', 'software', 'off'),
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
    Opt("exiftool_ignore_minor", "--exiftool-ignore-minor", _JXL2TIFF, "flag", "metadata",
        "expert", "Write past minor metadata errors",
        "Decode a JXL whose maker notes exiftool cannot parse: that block reaches the "
        "TIFF as it is, unchecked. Never with Delete originals.",
        legacy_key="extra.exiftool_ignore_minor",
        conflicts=("delete_source", "delete_skipped"), danger=True),
    Opt("jpeg_orientation", "--jpeg-orientation", _D({"jxl2jpeg"}), "value", "metadata",
        "advanced", "JPEG orientation",
        "A JXL whose photo was a rotated JPEG: original = as the camera wrote it "
        "(stored pixels + EXIF Orientation); upright = turned, Orientation 1.",
        choices=("original", "upright"), legacy_key="extra.jpeg_orientation"),
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
    "jxl_jpeg_transcoder:--ram": "no effect: accepted for compatibility (the transcoder picks memory or a temp file itself)",
    "jxl_jpeg_transcoder:--no-ram": "no effect: accepted for compatibility (the transcoder picks memory or a temp file itself)",
    "jxl_jpeg_transcoder:--to-srgb": "alias of --icc-profile sRGB, which the wrapper uses",
    # Never passed: the run's own answer is given another way.
    "jxl_tiff_encoder:--version": "not a run option",
    "jxl_tiff_decoder:--version": "not a run option",
    "jxl_jpeg_transcoder:--version": "not a run option",
    "jxl_recompressor:--version": "not a run option",
    # A loop for a program that sends the script its files one by one
    # (photo-backup), never a run of the wizard: refused in the expert flags.
    "jxl_tiff_encoder:--serve": "serve mode, for programs (photo-backup)",
    "jxl_tiff_decoder:--serve": "serve mode, for programs (photo-backup)",
    "jxl_jpeg_transcoder:--serve": "serve mode, for programs (photo-backup)",
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
    # A delete typed in the expert flags is a delete here too (rc6, D15): its
    # conflicts (--restamp-then-sync, --exiftool-ignore-minor) were met by
    # the child, with exit 2 after the HHMM.
    adv = dict(workflow.get('advanced_options') or {})
    _flags_ns = _child_flags(origin, dest, workflow.get('expert_flags'))
    if _flags_request_delete(workflow.get('expert_flags'), origin, dest):
        adv['delete_source'] = True
        if _flags_ns is not None and getattr(_flags_ns, 'delete_skipped', False):
            adv['delete_skipped'] = True
    ctx = _CmdCtx(origin, dest, workflow.get('input_dir'), None, workflow.get('mode'),
                  workflow.get('workers'), workflow, adv, False, None)
    on = {o.id for o in REGISTRY if _opt_is_on(o, ctx)}
    # Any option typed in the expert flags is "on" for the conflicts too (the
    # review of rc6, S1: --restamp-then-sync or --exiftool-ignore-minor typed
    # there next to a delete reached the child, refused after the HHMM). A
    # requirement stays the wizard's own options' rule.
    typed = _flags_set_options(workflow.get('expert_flags'), origin, dest)
    flag_on = {o.id for o in REGISTRY
               if ctx.direction in o.directions and o.flag in typed}
    if "delete_source" not in on | flag_on:
        # A --delete-skipped alone is inert (every script ignores it without
        # --delete-source): no conflict for it (rc6's second review, N2).
        flag_on.discard("delete_skipped")
    label = {o.id: o.label for o in REGISTRY}
    for o in REGISTRY:
        if o.id not in on | flag_on:
            continue
        bad = [label.get(c, c) for c in o.conflicts if c in on | flag_on]
        if bad:
            return f"'{o.label}' ({o.flag}) cannot run with: {', '.join(bad)}"
        if o.id not in on:
            continue
        missing = [label.get(r, r) for r in o.requires if r not in on | flag_on]
        if missing:
            return f"'{o.label}' ({o.flag}) needs: {', '.join(missing)}"
    return None


def _flags_set_options(flags: Optional[str], origin: Optional[str],
                       dest: Optional[str]) -> Set[str]:
    """The option strings of the child's parser ('--restamp-then-sync') that
    the expert flags SET: their argparse dest differs from the parser's own
    default — the child's parse, never the wizard's reading of the spelling
    (a flag's dest is not always its name: the decoder's --none is
    force_none). Empty when nothing can be parsed."""
    tokens = _split_expert_flags(flags or '')
    if not tokens or not origin or not dest:
        return set()
    try:
        parser = importlib.import_module(_child_module(origin, dest)).build_parser()
        parser.set_defaults(mode=_MODE_NOT_GIVEN)
    except Exception:
        return set()
    try:
        with redirect_stderr(io.StringIO()):
            ns, _x = parser.parse_known_args(["__jxlphoto_probe__"] + tokens)
            ns0, _y = parser.parse_known_args(["__jxlphoto_probe__"])
    except SystemExit:
        return set()
    return {opt for opt, action in parser._option_string_actions.items()
            if action.dest not in ("help", "version")
            and getattr(ns, action.dest, None) != getattr(ns0, action.dest, None)}


def _dest_is_source_file(source, dest_path, mode) -> bool:
    """A file Source whose Destination cell is the file itself (how the
    manifest loader normalizes an empty cell): it must NOT be sent as the
    output positional, or the child reads it as an output DIRECTORY named
    after the file. Modes 0 and 2 are the ones whose children honour an
    output positional: in mode 2 every child failed such a row with "1
    errors" and wrote nothing (#666); without it, each writes where its mode
    2 puts a single file."""
    if not dest_path or mode not in (0, 2):
        return False
    try:
        src_p = Path(source)
        if src_p.is_file():
            return (os.path.normcase(os.path.abspath(str(dest_path)))
                    == os.path.normcase(os.path.abspath(str(src_p))))
    except (OSError, ValueError):
        return False
    return False


def _child_plan_args(child, cmd: List[str]):
    """The child's own parse of the command line one run gives it: `cmd` is
    that run's argv as build_child_cmd builds it (quiet), the interpreter
    and the script left out. So the plan reads every flag the run gets —
    the input, the output positional, --mode, the transcoder's conversion
    and format flags, the rename, the resize and sharpening (#663),
    --output-name, the marker and folder flags and the expert flags (#664)
    — and cannot leave one out. What the wrapper hands the child's
    _planned_sources/_planned_pairs (refactor C1), inside _with_child_marker
    (_child_plan_globals). None when the child's parser refuses that
    command line: such a run writes nothing."""
    try:
        with redirect_stderr(io.StringIO()):
            return child.build_parser().parse_args([str(t) for t in cmd[2:]])
    except SystemExit:
        return None


def _child_plan_globals(ns, direction: str) -> Tuple:
    """(marker, subfolder, modes 6/7 output folder, exclusions) as the run's
    own parse gives them (_child_plan_args): what the child's main() copies
    into the module globals its finders and resolvers read, for
    _with_child_marker. Read from the parse, so a marker or a folder set in
    the expert flags moves the plan as it moves the run (#664). The output
    folder is the flag of the run's direction: --export-jpeg-folder for a
    JXL -> JPEG/PNG run, --export-jxl-folder elsewhere (a child without the
    flag: None, its setting stays)."""
    flag = ('export_jpeg_folder' if direction in ('jxl2jpeg', 'jxl2png')
            else 'export_jxl_folder')
    folder = getattr(ns, flag, None)
    if isinstance(folder, str):
        folder = folder.strip()        # as the children strip it
    return (getattr(ns, 'export_marker', None), getattr(ns, 'export_subfolder', None),
            folder or None, getattr(ns, 'exclude_folders', None) or None)


def _is_origin_file(f: Path, exts) -> bool:
    """A file of the run's origin format (unreadable = no)."""
    try:
        return f.is_file() and f.suffix.lower() in exts
    except OSError:
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
               default: Optional[str] = None, note: Optional[str] = None,
               title: Optional[str] = None,
               columns: Optional[List[str]] = None,
               columns_title: Optional[str] = None) -> str:
        """One key of `options` [(key, label)]; `note` is printed under the
        list, before the prompt; `title` heads the list (else the prompt).
        With `columns` (the headers of extra cells: ["45 MP", "24 MP"]) each
        option is (key, label, cell, ...) and the list gets a header row;
        `columns_title` is a line over those headers, flush right.
        An option with an empty key is a line to read, not to choose (shown
        grey, never accepted as an answer)."""
        raise NotImplementedError

    # The wizard's older questions keep their exact wording in each renderer
    # (a Rich prompt with its default shown by Rich, a plain one with the
    # default in brackets): one call, one qid, two texts.
    def ask_line(self, qid: str, rich: str, plain: str, default: Optional[str] = None,
                 choices: Optional[List[str]] = None) -> str:
        """A free answer (stripped); Enter = `default` when one is given."""
        raise NotImplementedError

    def ask_yes(self, qid: str, rich: str, plain: str, default: bool = False) -> bool:
        raise NotImplementedError

    def ask_raw(self, qid: str, prompt: str = "> ") -> str:
        """A line typed after a prompt the caller printed (the HHMM token);
        an end of input is an empty answer."""
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    def markup(self, rich: Optional[str], plain: Any = None) -> None:
        """Text with Rich styles; the plain renderer prints `plain`, or the
        text with its style tags removed (never a [FINAL] of a path).
        `rich=None` shows nothing in Rich, `plain=_SILENT` nothing in plain
        (the two renderers lay some screens out differently)."""
        raise NotImplementedError

    @property
    def is_rich(self) -> bool:
        """Tables and panels built by hand are drawn only by the Rich renderer."""
        return False

    def panel(self, body: str, title: str = "", style: str = "yellow",
              plain: Optional[str] = None) -> None:
        raise NotImplementedError

    def table(self, title: str, columns: List[str], rows: List[Tuple[str, ...]],
              head: Optional[List[str]] = None, notes: Optional[List[str]] = None,
              show_header: bool = True, markup: bool = False) -> None:
        """A framed table in Rich, aligned columns in plain text: `head` lines
        above it (plain text), `notes` below it. Cells are plain text, or Rich
        markup with `markup=True` (the plain renderer drops the styles)."""
        raise NotImplementedError

    def actions(self, groups: List[Tuple[str, List[Tuple[str, str]]]]) -> None:
        """The keys of a screen, one line per intent: [("Run", [("r", "RUN"),
        ...]), ...]."""
        raise NotImplementedError


def _plain_prompt(text: str) -> str:
    """A prompt with no blank line of its own: the renderer puts exactly one
    before every question (v2.23.0), so the eye finds where to type."""
    return text.lstrip("\n")


# One reading of a yes/no answer for both renderers (#649): yes or no in
# English or Portuguese, Enter for the question's default, anything else asks
# again. The plain renderer read any answer not starting with "n" as YES on a
# [Y/n] question ("sair" ran the job) and "sim" as NO in confirm(); the Rich
# one asked again on "yes" and "sim".
_YES_ANSWERS = frozenset({"y", "yes", "s", "sim"})
_NO_ANSWERS = frozenset({"n", "no", "nao", "não"})


def _yes_no(answer: str, default: Optional[bool]) -> Optional[bool]:
    """True/False for a yes/no answer, `default` for an empty one, None for
    anything else (the caller asks again)."""
    text = answer.strip().lower()
    if not text:
        return default
    if text in _YES_ANSWERS:
        return True
    if text in _NO_ANSWERS:
        return False
    return None


def _plain_table_lines(columns, rows, show_header=True):
    widths = [max([len(c)] + [len(str(r[i])) for r in rows]) if show_header
              else max([0] + [len(str(r[i])) for r in rows])
              for i, c in enumerate(columns)]
    out = []
    if show_header:
        out.append("  " + "  ".join(c.ljust(w) for c, w in zip(columns, widths)).rstrip())
    for r in rows:
        out.append("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip())
    return out


def _plain_action_lines(groups):
    return [f"{label:<8} " + "   ".join(f"[{k}] {t}" for k, t in keys)
            for label, keys in groups]


_SILENT = object()    # UI.markup: this renderer prints nothing


# grey70: the wizard's notes (v2.23.x). Rich's `dim` was too dark to read on
# Windows Terminal; a named colour needs no theme, so any Console renders it.
_STYLE_TAG = re.compile(
    r"\[/?(?:bold|dim|italic|underline|red|green|yellow|blue|cyan|magenta|white|black|grey70)"
    r"(?: (?:bold|dim|italic|underline|red|green|yellow|blue|cyan|magenta|white|black|grey70))*\]")


def _strip_styles(text: str) -> str:
    """`text` without Rich style tags; any other [...] (a folder name) stays."""
    return _STYLE_TAG.sub("", text)


class PlainUI(UI):
    def section(self, title):
        print(f"\n--- {title} ---")

    def line(self, text=""):
        print(text)

    def ask_text(self, qid, prompt, default=""):
        shown = f" [{default}]" if default else ""
        print()
        answer = input(f"{_plain_prompt(prompt)}{shown}: ").strip()
        return answer if answer else default

    def confirm(self, qid, prompt, default=False):
        while True:
            print()
            answer = _yes_no(input(f"{_plain_prompt(prompt)} [{'Y/n' if default else 'y/N'}]: "),
                             default)
            if answer is not None:
                return answer
            print("Answer y or n.")

    def choose(self, qid, prompt, options, default=None, note=None, title=None,
               columns=None, columns_title=None):
        keys = [o[0] for o in options if o[0]]
        if title:
            print(f"\n--- {title} ---")
        if columns:
            rows = [(f"[{o[0]}] {o[1]}" if o[0] else f"    {o[1]}",) + tuple(o[2:])
                    for o in options]
            lines = _plain_table_lines([""] + list(columns), rows)
            if columns_title:
                print(columns_title.rjust(len(lines[0].rstrip())))
            for line in lines:
                print(line)
        else:
            for k, label in options:
                print(f"  [{k}] {label}" if k else f"      {label}")
        if note:
            print(note)
        while True:
            print()
            answer = input(f"{_plain_prompt(prompt)}{f' [{default}]' if default else ''}: ").strip()
            if not answer and default is not None:
                return default
            if answer in keys:
                return answer
            print(f"Choose one of: {', '.join(keys)}")

    def ask_line(self, qid, rich, plain, default=None, choices=None):
        print()
        answer = input(_plain_prompt(plain)).strip()
        return answer if answer or default is None else default

    def ask_yes(self, qid, rich, plain, default=False):
        while True:
            print()
            answer = _yes_no(input(_plain_prompt(plain)), default)
            if answer is not None:
                return answer
            print("Answer y or n.")

    def markup(self, rich, plain=None):
        if plain is _SILENT or (plain is None and rich is None):
            return
        print(_strip_styles(rich) if plain is None else plain)

    def panel(self, body, title="", style="yellow", plain=None):
        print()
        if title:
            print(_strip_styles(title))
        print(_strip_styles(body) if plain is None else plain)

    def table(self, title, columns, rows, head=None, notes=None, show_header=True,
              markup=False):
        if markup:
            rows = [tuple(_strip_styles(str(v)) for v in r) for r in rows]
        # Two blank lines before a new block: no frame tells it apart here.
        print("\n")
        if title:
            print(f"--- {title} ---")
        for line in head or []:
            print(line)
        for line in _plain_table_lines(columns, rows, show_header):
            print(line)
        for line in notes or []:
            print(f"  · {line}")

    def actions(self, groups):
        print()
        for line in _plain_action_lines(groups):
            print(line)


if RICH_AVAILABLE:
    from rich.prompt import InvalidResponse

    class _YesNoConfirm(Confirm):
        """Rich's Confirm reading the answer with _yes_no (#649): the same
        "[y/n] (y)" on screen and the same "Please enter Y or N" for anything
        it does not take."""

        def process_response(self, value: str) -> bool:
            answer = _yes_no(value, None)
            if answer is None:
                raise InvalidResponse(self.validate_error_message)
            return answer


class RichUI(UI):
    def section(self, title):
        console.print(f"\n[bold cyan]{_rich_safe(title)}[/bold cyan]")

    def line(self, text=""):
        console.print(_rich_safe(text))

    def warn(self, text):
        console.print(f"[yellow]{_rich_safe(text)}[/yellow]")

    @staticmethod
    def _default_kw(default):
        """Prompt.ask's default: none for an empty one (Rich would show "()"),
        and a long one (a path) on its own line above the question, where it
        does not wrap the prompt in two."""
        if default is None or default == "":
            return {}
        if len(str(default)) > 40:
            console.print(f"  [grey70]Enter = {_rich_safe(str(default))}[/grey70]")
            return {"default": default, "show_default": False}
        return {"default": default}

    def ask_text(self, qid, prompt, default=""):
        console.print()
        return (Prompt.ask(_rich_safe(_plain_prompt(prompt)), **self._default_kw(default))
                or default or "").strip()

    def confirm(self, qid, prompt, default=False):
        console.print()
        return _YesNoConfirm.ask(_rich_safe(_plain_prompt(prompt)), default=default)

    def choose(self, qid, prompt, options, default=None, note=None, title=None,
               columns=None, columns_title=None):
        t = Table(show_header=bool(columns), header_style="bold cyan", box=None,
                  padding=(0, 1), title=_rich_safe(columns_title) if columns_title else None,
                  title_justify="right", title_style="cyan")
        t.add_column(style="bold cyan", justify="right")
        t.add_column()
        for c in columns or []:
            t.add_column(_rich_safe(c), justify="right")
        for o in options:
            t.add_row(*[_rich_safe(str(v)) for v in o], style=None if o[0] else "grey70")
        console.print(Panel(t, title=f"[bold]{_rich_safe(title or prompt)}[/bold]",
                            border_style="cyan", box=BOX_ROUNDED, expand=False))
        if note:
            console.print(f"[grey70]{_rich_safe(note)}[/grey70]")
        console.print()
        return Prompt.ask(_rich_safe(prompt), choices=[o[0] for o in options if o[0]],
                          default=default)

    def ask_line(self, qid, rich, plain, default=None, choices=None):
        console.print()
        kw = self._default_kw(default)
        if choices:
            # Letters are taken in either case; each is shown once.
            shown, seen = [], set()
            for c in choices:
                if c.lower() not in seen:
                    seen.add(c.lower())
                    shown.append(c)
            kw["choices"] = shown
            kw["case_sensitive"] = False
        answer = Prompt.ask(_plain_prompt(rich), **kw)
        return (answer or "").strip()

    def ask_yes(self, qid, rich, plain, default=False):
        console.print()
        return _YesNoConfirm.ask(_plain_prompt(rich), default=default)

    def table(self, title, columns, rows, head=None, notes=None, show_header=True,
              markup=False):
        t = Table(show_header=show_header, header_style="bold cyan", box=BOX_SIMPLE,
                  padding=(0, 1))
        for i, c in enumerate(columns):
            _key = i == 0 and c in ("#", "Key")
            t.add_column(c, style="bold cyan" if _key else ("cyan" if i == 0 else None),
                         justify="right" if _key else "left")
        for r in rows:
            t.add_row(*[str(v) if markup else _rich_safe(str(v)) for v in r])
        parts = [_rich_safe(line) for line in head or []]
        parts.append(t)
        parts += [f"[grey70]· {_rich_safe(line)}[/grey70]" for line in notes or []]
        console.print()
        console.print(Panel(Group(*parts), title=f"[bold]{_rich_safe(title)}[/bold]",
                            border_style="green", box=BOX_ROUNDED, expand=False))

    def actions(self, groups):
        g = Table.grid(padding=(0, 2))
        g.add_column(style="bold cyan")
        g.add_column()
        for label, keys in groups:
            g.add_row(label, "   ".join(
                f"[bold]\\[{_rich_safe(k)}][/bold] {_rich_safe(t)}" for k, t in keys))
        console.print(g)

    @property
    def is_rich(self):
        return True

    def markup(self, rich, plain=None):
        if rich is not None:
            console.print(rich)

    def panel(self, body, title="", style="yellow", plain=None):
        console.print(Panel(body, title=title or None, border_style=style))


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

    def choose(self, qid, prompt, options, default=None, note=None, title=None,
               columns=None, columns_title=None):
        if title:
            self.output.append(f"== {title}")
        if note:
            self.output.append(note)
        v = self._next(qid)
        v = default if v is None else str(v)
        if v not in [o[0] for o in options if o[0]]:
            raise AssertionError(f"ScriptedUI: {v!r} is not an option of {qid!r}")
        return v

    def ask_line(self, qid, rich, plain, default=None, choices=None):
        v = self._next(qid)
        v = (default or "") if v is None else str(v)
        if choices and v not in choices:
            raise AssertionError(f"ScriptedUI: {v!r} is not an option of {qid!r}")
        return v

    def ask_yes(self, qid, rich, plain, default=False):
        v = self._next(qid)
        return default if v is None else bool(v)

    def ask_raw(self, qid, prompt="> "):
        v = self._next(qid)
        return "" if v is None else str(v)

    def markup(self, rich, plain=None):
        if plain is _SILENT:
            plain = None
            if rich is None:
                return
        if plain is None and rich is None:
            return
        self.output.append(_strip_styles(rich) if plain is None else plain)

    def panel(self, body, title="", style="yellow", plain=None):
        self.output.append(_strip_styles(body) if plain is None else plain)

    def table(self, title, columns, rows, head=None, notes=None, show_header=True,
              markup=False):
        if markup:
            rows = [tuple(_strip_styles(str(v)) for v in r) for r in rows]
        if title:
            self.output.append(f"== {title}")
        self.output.extend(head or [])
        self.output.extend(_plain_table_lines(columns, rows, show_header))
        self.output.extend(f"  · {line}" for line in notes or [])

    def actions(self, groups):
        self.output.extend(_plain_action_lines(groups))


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


def _refresh_child_overlays() -> None:
    """The scripts this wizard has already imported take the overlay file
    again, after menu S changed it. Each one applied the file when it was
    imported, and the wizard reads its settings from it (_child_setting, the
    plan's functions) and sends some of them on the command line, where they
    win over the file: a change made in menu S reached a job only after the
    wizard was restarted (rc6, Fable's second sweep D5; #709 had fixed the
    task memory's side). From the values the script had before the overlay
    (_SETTINGS_OVERLAY["script"]), exactly as at its import: a setting taken
    out of the file goes back to the script's value. The per-run snapshot
    (_RUN_DEFAULTS) follows, and so does the encoder's list of the decoder's
    folder names, built from that script's settings at import; the decoder's
    and the recompressor's list of the other tools' folder names is made again
    at its next use."""
    for module, _label in _OVERLAY_SCRIPTS:
        mod = sys.modules.get(module)
        if mod is None:
            continue            # not imported yet: its import reads the new file
        try:
            before = mod._SETTINGS_OVERLAY.get("script")
            specs, derived = mod._OVERLAY_SETTINGS, mod._OVERLAY_DERIVED
        except Exception:
            continue
        if not isinstance(before, dict):
            continue
        namespace = dict(before)
        result = mod._apply_settings_overlay(namespace, module, specs, derived)
        run_defaults = getattr(mod, "_RUN_DEFAULTS", None)
        for name, value in namespace.items():
            if name not in specs:
                continue
            setattr(mod, name, value)
            if isinstance(run_defaults, dict) and name in run_defaults:
                run_defaults[name] = value
        mod._SETTINGS_OVERLAY = result
        # The decoder's and the recompressor's lazy list of the OTHER tools'
        # folder names (#565), read from those scripts' settings and the
        # overlay once: made again at its next use (the review of rc6, S3).
        if hasattr(mod, "_other_tool_folders_cache"):
            mod._other_tool_folders_cache = None
    enc = sys.modules.get("jxl_tiff_encoder")
    if enc is not None and callable(getattr(enc, "_decoder_output_folders", None)):
        try:
            enc._DECODER_OUTPUT_FOLDERS = enc._decoder_output_folders()
        except Exception:
            pass

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
# The conversion types more than one task offers (jxl_to_jpeg_auto: "Export
# JPEGs" and "Get the original JPEGs back"): a job of one names no task.
_CONVERSIONS_OF_TWO_TASKS = frozenset(
    conv for conv in {c for t in TASKS for c in t[4]}
    if sum(conv in t[4] for t in TASKS) > 1)


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


# --- What a new job of a task starts from (#704-#709) ------------------------
# A new job started from the last job of ANY task: after an 8-bit PNG export
# "Restore TIFFs" asked the decoder for 8-bit TIFFs (#704), after copies made
# in sRGB_JXL the next masters went there (#705). Each task now remembers its
# own last run (ToolConfig.task_memory, _task_memory_entry), read through
# InteractiveMenu._task_memory.

# The answers one job may set and the next must not inherit: each save clears
# them, then writes what THIS run staged — a "Repeat last job" replayed the
# --provenance adopt / --no-overwrite-guard of an earlier job (#706, #707; a
# delete with the guard off is refused by every script, after the HHMM).
_RUN_SCOPED_SESSION_FIELDS = ('last_verify_roundtrip', 'last_delete_skipped',
                              'last_provenance', 'last_adopt_scan',
                              'last_overwrite_guard')
# The [D] answers. In a task's memory they come from its last run WITH a
# delete (a sync in between keeps them), the guard's from its last run
# without one (a delete never offers the guard).
_DELETE_ANSWER_FIELDS = ('last_verify_roundtrip', 'last_delete_skipped',
                         'last_provenance', 'last_adopt_scan')


def _script_own_fields(origin: Optional[str], dest: Optional[str]):
    """(last_* field, child module, _child_default option, inverted) of the
    values a new job of this direction takes from the script when its task
    has no answer of its own. A remembered value EQUAL to the script's
    setting is kept as None, "the script's own", so a later change of that
    setting (menu S, the overlay) reaches the next job (#709: the last job's
    value went on every command line after it, and "script default" lasted
    one run)."""
    rows = [('last_overwrite_guard', _child_module(origin, dest), 'overwrite_guard', False)]
    if (origin, dest) == ('tiff', 'jxl'):
        rows += [('last_multipage_mode', 'jxl_tiff_encoder', 'multipage_mode', False),
                 ('last_thumbnail_mode', 'jxl_tiff_encoder', 'thumbnail_mode', False),
                 ('last_thumbnail_suffix', 'jxl_tiff_encoder', 'thumbnail_suffix', False),
                 ('last_use_ram', 'jxl_tiff_encoder', 'use_ram', False)]
    elif (origin, dest) == ('jxl', 'tiff'):
        rows += [('last_thumbnail_handling', 'jxl_tiff_decoder', 'thumbnail_handling', False),
                 ('last_thumbnail_suffix', 'jxl_tiff_decoder', 'thumbnail_suffix', False),
                 ('last_depth_policy', 'jxl_tiff_decoder', 'depth_policy', False),
                 ('last_no_reconstruct_multipage', 'jxl_tiff_decoder',
                  'reconstruct_multipage', True),
                 ('last_compression', 'jxl_tiff_decoder', 'compression', False),
                 ('last_add_preview', 'jxl_tiff_decoder', 'add_preview', False),
                 ('last_bit_depth', 'jxl_tiff_decoder', 'bit_depth', False)]
    elif dest == 'png':
        rows.append(('last_bit_depth', 'jxl_jpeg_transcoder', 'bit_depth', False))
    return rows


def _task_memory_entry(snapshot: Dict, workflow: Dict, previous: Optional[Dict]) -> Dict:
    """What a run leaves for the next new job of its task: the session
    snapshot save_last_session just wrote, with the run's OWN value for the
    options a None does not clear there ("script default" chosen on [a]), the
    [D] answers of a run without a delete carried over from the task's last
    one (and the guard's of a run with one), and every value equal to the
    script's setting kept as None (_script_own_fields)."""
    entry = dict(snapshot)
    previous = previous if isinstance(previous, dict) else {}
    origin, dest = workflow.get('origin_format'), workflow.get('dest_format')
    adv = workflow.get('advanced_options') or {}
    if (origin, dest) == ('tiff', 'jxl'):
        for key in ('multipage_mode', 'thumbnail_mode', 'thumbnail_suffix'):
            entry['last_' + key] = adv.get(key)
    elif (origin, dest) == ('jxl', 'tiff'):
        for key in ('thumbnail_handling', 'thumbnail_suffix', 'depth_policy',
                    'no_reconstruct_multipage'):
            entry['last_' + key] = adv.get(key)
    if dest in ('tiff', 'png'):
        entry['last_bit_depth'] = workflow.get('bit_depth')
    if adv.get('delete_source'):
        entry['last_overwrite_guard'] = previous.get('last_overwrite_guard')
        for f in _DELETE_ANSWER_FIELDS:       # a question this run did not ask
            if entry.get(f) is None:
                entry[f] = previous.get(f)
    else:
        for f in _DELETE_ANSWER_FIELDS:
            entry[f] = previous.get(f)
    for f, module, option, inverted in _script_own_fields(origin, dest):
        value = entry.get(f)
        if value is None:
            continue
        own = _child_default(module, option)
        if (not own if inverted else own) == value:
            entry[f] = None
    return entry


class _TaskMemory:
    """The config as a new job of one task sees it: every last_* field is
    that task's own (None when the task never ran since #704-#709), every
    other field (the Settings) is the config's."""
    __slots__ = ('_config', '_entry')

    def __init__(self, config, entry: Optional[Dict]):
        self._config = config
        self._entry = entry if isinstance(entry, dict) else {}

    def __getattr__(self, name):
        if name.startswith('last_'):
            return self._entry.get(name)
        return getattr(self._config, name)


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
    # A delete flag with a value (`--delete-source=1`) cannot run: the child's
    # argparse exits 2 (store_true takes none) before it does anything.
    # Caught ahead of every gate, so the user is not charged an HHMM token
    # for a doomed run.
    _amb = _flags_ambiguous_delete(wf.get('expert_flags'))
    if _amb:
        menu._print_error(
            f"Expert flag {_amb!r} cannot run: the delete flags take no value "
            f"(the child scripts reject '=...' on them). Write it without the value.")
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


def _gate_guard_with_delete(menu, wf, ctx) -> bool:
    """A run that deletes never runs with the overwrite guard off: every
    script refuses the pair (exit 2). A --delete-source typed in the expert
    flags counts like [D] (rc6, Fable's second sweep D15): the 'off' the job
    took from its task's memory before the flags were typed does not apply
    (_append_overwrite_guard_flag leaves it out, as the [D] review and the
    #707 replay do), said here; a --no-overwrite-guard typed next to a delete
    is refused before the HHMM — the child refused it after."""
    if not _job_deletes(wf):
        return False
    origin, dest = wf.get('origin_format'), wf.get('dest_format')
    if _flags_turn_guard_off(wf.get('expert_flags'), origin, dest):
        menu._print_error("--no-overwrite-guard cannot run with a delete: every script "
                          "refuses the pair. Remove one of them; nothing was started.")
        return True
    # A maintenance mode typed in the expert flags (menus 5-8: --restamp-moved,
    # --refresh-explorer-thumbnails, ...) never runs with a delete either; the
    # script refused the pair after the HHMM (rc6's second review, N3).
    _module = _child_module(origin, dest) if origin and dest else ""
    _maintenance = sorted(f for f in _flags_set_options(wf.get('expert_flags'), origin, dest)
                          if f"{_module}:{f}" in MAINTENANCE_FLAGS)
    if _maintenance:
        menu._print_error(f"{', '.join(_maintenance)} is a maintenance mode (menus 5-8) and "
                          f"never runs with a delete. Remove one of them; nothing was started.")
        return True
    if ((wf.get('advanced_options') or {}).get('overwrite_guard') is False
            and not wf.get('_guard_note_shown')):
        wf['_guard_note_shown'] = True
        menu.ui.warn("The overwrite guard stays on wherever this run deletes sources: "
                     "an earlier choice of 'off' does not apply there.")
    return False


def _gate_mode_in_expert_flags(menu, wf, ctx) -> bool:
    _mode_flag = _flags_set_mode(wf.get('expert_flags'), wf.get('origin_format'),
                                 wf.get('dest_format'))
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
    # A JXL -> JXL derivative typed in the expert flags has no in-place form
    # either (rc6, D15: mode 0 + [x] --output-icc reached the recompressor).
    _shaping_pre = (_has_derivative_options(_adv_pre)
                    or (origin == 'jxl' and dest == 'jxl'
                        and _flags_shape_derivative(wf.get('expert_flags'), origin, dest)))
    _delete_pre = bool(_adv_pre.get('delete_source')
                       or _flags_request_delete(wf.get('expert_flags'), origin, dest))
    _conv_pre = wf.get('conversion_type', '')
    # A derivative typed in the expert flags (rc6, D15: an --output-icc or a
    # --resize-long next to [D] reached the child, which refused it after the
    # HHMM; the review's S1 and N3: the JPEG -> JXL convert too, never the
    # bit-exact route's ignored profile).
    if _delete_pre and _flags_derivative_refused(wf):
        menu._print_error("The expert flags make this run write a DERIVATIVE (a colour "
                          "conversion, a resize or a sharpening), which never deletes its "
                          "source — drop the delete option or those flags.")
        return True
    # The decoder's --none typed there is the decode mode 'none' (W4 below).
    _flags_ns = _child_flags(origin, dest, wf.get('expert_flags'))
    _none_pre = bool(_adv_pre.get('none')
                     or (_flags_ns is not None and getattr(_flags_ns, 'force_none', False)))
    if origin == 'jxl' and dest == 'jxl':
        # In place: modes 0/8, and a mode-2 output folder inside the input that
        # already holds JXLs (rc6's second review, N1: the in-place HHMM was
        # asked, then the recompressor refused the derivative, exit 2).
        _in_place_pre = _jxl_run_replaces_in_place(
            origin, dest, wf.get('mode'), wf.get('input_dir'),
            (wf.get('mode_config') or {}).get('output_dir'),
            _mode_config_with_marker(wf.get('mode_config'), _menu_marker(menu)),
            wf.get('expert_flags'))
        if _shaping_pre:
            if _in_place_pre:
                menu._print_error("Derivatives (colour conversion/resize/sharpening) "
                                  "cannot run in place (modes 0/8, or an output folder "
                                  "inside the input that already holds JXLs) — pick "
                                  "another mode or output folder.")
                return True
            if _delete_pre:
                menu._print_error("A derivative never deletes its source — drop the "
                                  "delete option.")
                return True
        if _adv_pre.get('rename_from') and _in_place_pre:
            menu._print_error("--rename-from cannot be used in place (modes 0/8, or an "
                              "output folder inside the input that already holds "
                              "JXLs): the renamed file would sit beside its source "
                              "and be re-processed as a new input.")
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
        if _none_pre:
            menu._print_error("Decode mode 'none' writes no colour profile and no "
                              "XMP/IPTC — the TIFF is not a copy of the master, so "
                              "it cannot delete the JXLs. Drop the delete option "
                              "(or pick another decode mode).")
            return True
        if str(_bit_depth_or_default(wf, 'tiff')) == '8' or _adv_pre.get('basic'):
            _note("Bit depth 8 / decode mode 'basic' makes a degraded copy: the "
                  "decoder KEEPS the JXLs whose master is deeper than 8 bits or "
                  "whose real profile lives in XMP.")
    if (wf.get('icc_profile') and _delete_pre
            and _direction(origin, dest) in _TRANSCODER_DIRECTIONS
            and _conv_pre not in _BIT_EXACT_CONVERSIONS):
        # W6 (2026-10-08 audit): a colour conversion is a derivative too. The
        # transcoder refuses a profile next to a delete on every route but the
        # bit-exact one — JPEG/PNG -> JXL too, where it ignores the profile
        # (rc8, Fable's S3: only the decode routes were refused here, and the
        # child exited 2 after the HHMM token).
        if origin == 'jxl':
            menu._print_error("Colour conversion (Convert to sRGB) writes a derivative "
                              "that never deletes its source — drop the delete option "
                              "(or the conversion).")
        else:
            menu._print_error("An output profile is ignored on JPEG/PNG → JXL, and the "
                              "transcoder refuses one next to a delete — drop the "
                              "delete option (or the profile).")
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
    _flags_derived = (origin == 'jxl' and dest == 'jxl'
                      and _flags_shape_derivative(wf.get('expert_flags'), origin, dest))
    _derived_rows = [i + 1 for i, (_w, _a) in enumerate(row_effs)
                     if _has_derivative_options(_a) or _flags_derived]
    _rename_rows = [i + 1 for i, (_w, _a) in enumerate(row_effs) if _a.get('rename_from')]
    _flags_delete = _flags_request_delete(wf.get('expert_flags'), origin, dest)
    _manifest_delete = bool(advanced.get('delete_source') or _flags_delete)
    if _manifest_delete and _flags_derivative_refused(wf):
        # rc6, D15: a derivative typed in the expert flags makes every row one.
        menu._print_error("The expert flags make every row write a DERIVATIVE (a colour "
                          "conversion, a resize or a sharpening), which never deletes its "
                          "source — drop the delete option or those flags.")
        return True
    _flags_ns = _child_flags(origin, dest, wf.get('expert_flags'))
    if (_manifest_delete and origin == 'jxl' and dest == 'tiff'
            and (advanced.get('none')
                 or (_flags_ns is not None and getattr(_flags_ns, 'force_none', False)))):
        # W4 for a manifest (the review of rc6, S1): the decoder refuses a delete
        # with decode mode 'none' (no colour profile, no XMP/IPTC: not a copy of
        # the master) — after the HHMM, every row.
        menu._print_error("Decode mode 'none' writes no colour profile and no XMP/IPTC — "
                          "the TIFFs are not copies of the masters, so they cannot delete "
                          "the JXLs. Drop the delete option (or pick another decode mode).")
        return True
    if _flags_delete and origin == 'jxl' and dest in ('jpeg', 'png'):
        # A --delete-source typed in the expert flags reaches EVERY row, and
        # the wizard cannot take it out of the rows that never delete, as it
        # does for [D] (_policy_delete): the child refused each of them after
        # the HHMM, a "WRAPPER bug" in the message (rc6, D15, Fable's p10).
        _never_rows = [i + 1 for i, (_w, _a) in enumerate(row_effs)
                       if _has_derivative_options(_a) or _w.get('icc_profile')
                       or _lossy_decode_never_deletes(_w)]
        if _never_rows:
            menu._print_error(
                "--delete-source in the expert flags reaches every row, and row(s) "
                f"{', '.join(map(str, _never_rows[:10]))} never delete their source (a "
                "lossy decode or a derivative). Use 'Delete originals' ([D]) instead — "
                "the wizard leaves it out of those rows — or remove the flag.")
            return True
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


def _gate_manifest_lossless_recovery(menu, wf, ctx) -> bool:
    """A bit-exact JPEG reconstruction keeps the JPEG's bytes and its name:
    a manifest row that resizes, sharpens or renames cannot have one (the
    child would refuse per file), so it is refused before the token. An
    OutputICC there is only a warning: the recovery is what was asked."""
    if not (ctx['origin'] == 'jxl' and ctx['dest'] in ('jpeg', 'png')
            and wf.get('conversion_type') == 'jxl_to_jpeg_lossless'):
        return False
    # A bit-exact JPEG reconstruction cannot be resized/sharpened; the
    # child would refuse per file — refuse here, before the token.
    _bad = [i + 1 for i, (_w, _a) in enumerate(ctx['row_effs'])
            if _a.get('resize_mode') or (_a.get('sharpen') or 'none') != 'none']
    if _bad:
        menu._print_error("A bit-exact JPEG reconstruction cannot be "
                          "resized/sharpened — pick the lossy JXL→JPEG "
                          "conversion for that "
                          f"(row(s): {', '.join(map(str, _bad))}).")
        return True
    # --rename-from is just as impossible: the recovered JPEG keeps
    # the ORIGINAL file name (the child ignores the flag on the
    # lossless path), so a rename would only fool the collision scan
    # into modelling outputs the run never writes. Refuse it too.
    _ren = [i + 1 for i, (_w, _a) in enumerate(ctx['row_effs'])
            if _a.get('rename_from')]
    if _ren:
        menu._print_error("A bit-exact JPEG reconstruction keeps the "
                          "original file names — --rename-from does not "
                          "apply to the lossless JXL→JPEG recovery "
                          f"(row(s): {', '.join(map(str, _ren))}). Pick "
                          "the lossy conversion for that.")
        return True
    # OutputICC is likewise ignored on the lossless path (there is no
    # pixel to convert) — warn, don't refuse: the recovery itself is
    # still exactly what was asked.
    _icc = [i + 1 for i, (_w, _a) in enumerate(ctx['row_effs'])
            if _a.get('output_icc')]
    if _icc:
        _msg = (f"OutputICC is ignored by a bit-exact JPEG reconstruction "
                f"(row(s): {', '.join(map(str, _icc))}) — the recovered "
                "JPEG keeps the original bytes, colour included.")
        menu.ui.markup(f"[yellow]{_rich_safe(_msg)}[/yellow]", f"WARNING: {_msg}")
    return False


# The manifest's own steps (InteractiveMenu._manifest_step_*), reached through
# the table. Each adapter reads its ctx keys FIRST (a path run with a foreign
# context raises KeyError before it touches `menu`), then calls the step;
# True = refused (the step has printed why).
def _gate_manifest_mode7_subfolder(menu, wf, ctx) -> bool:
    """A mode-7 row whose subfolder cannot be told (the step asks or refuses;
    it may fill the run's export_subfolder)."""
    args = (ctx['resolved_entries'], ctx['row_opts_all'], ctx['row_markers'])
    return not menu._manifest_step_mode7_subfolder(wf, *args)


def _gate_manifest_output_folder_names(menu, wf, ctx) -> bool:
    args = (ctx['origin'], ctx['dest'], ctx['resolved_entries'], ctx['row_export'])
    return not menu._manifest_step_output_folder_names(*args)


def _gate_manifest_source_overlaps(menu, wf, ctx) -> bool:
    args = (ctx['resolved_entries'],)
    return not menu._manifest_step_source_overlaps(wf, *args)


def _gate_manifest_output_collisions(menu, wf, ctx) -> bool:
    args = (ctx['origin'], ctx['dest'], ctx['resolved_entries'], ctx['analyzer'],
            ctx['marker'], ctx['row_export'], ctx['row_effs'], ctx['cross_out_folders'])
    return not menu._manifest_step_output_collisions(wf, *args)


def _gate_manifest_output_feeds_source(menu, wf, ctx) -> bool:
    args = (ctx['resolved_entries'], ctx['cross_out_folders'])
    return not menu._manifest_step_output_feeds_source(wf, *args)


def _gate_manifest_in_place_unattended(menu, wf, ctx) -> bool:
    args = (ctx['any_in_place_recompress'], ctx['dry_run'])
    return not menu._manifest_step_in_place_unattended(wf, *args)


def _gate_preset_unattended(menu, wf, ctx) -> bool:
    """A destructive preset cannot run unattended: the delete gates ask for an
    HHMM token nobody can type, and skipping them would let a scheduled task
    delete originals on its own. Three ways a stored run reaches a deletion
    are checked: delete_source in ANY mode, --delete-source in the expert
    flags, and a JXL -> JXL run that replaces its sources in place
    (_jxl_run_replaces_in_place: modes 0/8, or a mode-2 output folder inside
    the input that already holds JXLs; the recompressor's confirmation would
    read an EOF stdin)."""
    if ctx.get('dry_run'):
        return False
    session = ctx['session']
    _adv_delete = bool((session.get('last_advanced_options') or {}).get('delete_source'))
    _flag_delete = _flags_request_delete(session.get('last_expert_flags'),
                                         ctx['origin'], ctx['dest'])
    # The run's own in-place test (the review of rc6, M1): a mode-2 preset
    # whose output folder, inside its input folder, holds JXLs replaces them.
    _in_place_recompress = _jxl_run_replaces_in_place(
        ctx['origin'], ctx['dest'], ctx['mode'], session.get('last_input_dir'),
        (session.get('last_mode_config') or {}).get('output_dir'),
        _mode_config_with_marker(session.get('last_mode_config'), _menu_marker(menu)),
        session.get('last_expert_flags'))
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
# and stops at the first refusal. A manifest's own steps (`_manifest_step_*`:
# they ask a question or need the run's intermediate state) are rules too, on
# the paths `manifest` / `manifest_outputs` / `manifest_in_place`, called where
# each state exists.
_GATE_RULES: Tuple[Tuple[str, frozenset, Callable], ...] = (
    ("ambiguous_delete_flags", frozenset({"common"}), _gate_ambiguous_delete_flags),
    ("expert_flags_parse", frozenset({"common"}), _gate_expert_flags_parse),
    ("registry_rules", frozenset({"common"}), _gate_registry_rules),
    ("guard_with_delete", frozenset({"common"}), _gate_guard_with_delete),
    ("mode_in_expert_flags", frozenset({"common"}), _gate_mode_in_expert_flags),
    ("direct_derivatives", frozenset({"direct"}), _gate_direct_derivatives),
    ("manifest_derivatives", frozenset({"manifest"}), _gate_manifest_derivatives),
    ("manifest_lossless_recovery", frozenset({"manifest"}),
     _gate_manifest_lossless_recovery),
    ("manifest_mode7_subfolder", frozenset({"manifest"}), _gate_manifest_mode7_subfolder),
    ("manifest_output_folder_names", frozenset({"manifest_outputs"}),
     _gate_manifest_output_folder_names),
    ("manifest_source_overlaps", frozenset({"manifest_outputs"}),
     _gate_manifest_source_overlaps),
    ("manifest_output_collisions", frozenset({"manifest_outputs"}),
     _gate_manifest_output_collisions),
    ("manifest_output_feeds_source", frozenset({"manifest_outputs"}),
     _gate_manifest_output_feeds_source),
    ("manifest_in_place_unattended", frozenset({"manifest_in_place"}),
     _gate_manifest_in_place_unattended),
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
                title=f"[bold blue]jxl-photo {TOOLKIT_VERSION} — tools found[/bold blue]",
                border_style="blue"
            ))
            console.print(grid)
        else:
            print("=" * 60)
            print(f"jxl-photo {TOOLKIT_VERSION} — tools found: {status_line}")
            print("=" * 60)

    def show_main_menu(self, has_last_session: bool) -> str:
        """Display main menu - NO DEFAULT"""
        options = []

        options.append(("", "CONVERT", None))
        if has_last_session:
            _c = self.config.config
            _what = _task_label(_c.last_origin_format, _c.last_dest_format,
                                _c.last_conversion_type)
            _where = Path(_c.last_input_dir).name if _c.last_input_dir else ""
            last_info = (f": {_what}, mode {_c.last_output_mode or '?'}"
                         + (f", {_where}" if _where else ""))
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
                    options.append(("2", f"Repeat last job: folder list (manifest: {Path(saved_manifest).name})", True))
                else:
                    options.append(("2", "Repeat last job (its folder list CSV is gone)", False))
            else:
                options.append(("2", f"Repeat last job{last_info}", True))
        else:
            options.append(("1", "New job: archive, restore, export, recompress...", True))
            options.append(("2", "Repeat last job", False))

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
            ("D", "Dependencies and new releases: check again", True),
        ])
        _poll = getattr(self, '_update_poll', None)
        if _poll and not getattr(self, '_update_available', None):
            _late = _poll()
            if _late:
                self.ui.markup(f"[yellow]{_rich_safe(_late[0])}[/yellow]", _late[0])
                self._update_available = (self.config.config.update_latest_seen, _late[1])
        if getattr(self, '_thumb_update', False):
            options.append(("T", "Update jxl-thumb (opens its releases page)", True))
        elif getattr(self, '_thumb_tip', False):
            options.append(("T", "Get jxl-thumb: fast thumbnails in Explorer "
                                 "(opens its page)", True))
        if getattr(self, '_update_available', None):
            options.append(("U", f"Update available: {self._update_available[0]} — "
                                 f"open the release page", True))
        options.extend([
            ("0", "Exit", True),
        ])
        # The letters menus R/C/S had in v2.22.0_beta1 still work (not shown).
        aliases = {"R": "5", "C": "8", "S": "S"}

        ui = self.ui
        if ui.is_rich:
            table = Table(show_header=False, box=None)
            table.add_column("Key", style="bold cyan")
            table.add_column("Option")
            table.add_column("Status", justify="center")

            for key, desc, available in options:
                if available is None:
                    table.add_row("", f"[bold]{desc}[/bold]", "")
                    continue
                status_str = "" if available else "[grey70](unavailable)[/grey70]"
                table.add_row(key, desc, status_str)

            console.print(Panel(table, title="Main Menu", border_style="green"))
        else:
            ui.markup(None, "\n--- Main Menu ---")
            for key, desc, available in options:
                if available is None:
                    ui.markup(None, desc)
                    continue
                status_str = "" if available else " [UNAVAILABLE]"
                ui.markup(None, f"[{key}] {desc}{status_str}")

        valid_choices = [o[0] for o in options if o[2]]
        while True:
            # D (and the old R/C/S) are letters: either case is taken
            # (Rich's own choices= check would refuse a lower-case 'd').
            choice = ui.ask_line("menu.main", "Select option", "\nSelect option: ").upper()
            choice = aliases.get(choice, choice)
            if choice in valid_choices or choice == "S":
                return choice
            ui.markup(f"[red]Choose one of: {', '.join(valid_choices)}[/red]",
                      f"Invalid choice. Valid options: {', '.join(valid_choices)}")

    @property
    def ui(self) -> "UI":
        """Every question of the wizard goes through this (tests set `_ui`).
        Without one set, the renderer follows RICH_AVAILABLE at each call:
        decided once, a run whose Rich was turned off afterwards kept it."""
        ui = self.__dict__.get('_ui')
        if ui is None:
            return RichUI() if (RICH_AVAILABLE and console) else PlainUI()
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
            numbered = {}
            table_rows = []
            for gid, title in GROUPS:
                mine = [o for o in rows if o.group == gid]
                if not mine:
                    continue
                table_rows.append(("", f"[bold]{_rich_safe(title)}[/bold]", "", ""))
                for o in mine:
                    n = str(len(numbered) + 1)
                    numbered[n] = o
                    tag = "" if _screen_editable(o) else " (in the review)"
                    mark = " [red](!)[/red]" if o.danger else ""
                    table_rows.append((n, f"{_rich_safe(o.label)}{mark}",
                                       _rich_safe(_option_shown(_display_value(o, ctx))),
                                       f"[grey70]{_rich_safe(o.flag + tag)}[/grey70]"))
            ui.table(f"All options — {origin.upper()} → {dest.upper()}"
                     + (f" (filter: {flt})" if flt else ""),
                     ["#", "Option", "Value", "Flag"], table_rows,
                     notes=["(!) relaxes a safety check: it asks before turning on."],
                     markup=True)
            answer = ui.ask_text("opt.screen", "Number to change, /text to filter, "
                                 "Enter or 0 to go back", "").strip()
            if not answer or answer.lower() in ("0", "b"):
                return
            if answer.startswith("/"):
                flt = answer[1:].strip().lower()
                continue
            o = numbered.get(answer)
            if o is None:
                ui.warn(f"No option {answer!r}.")
                continue
            if not _screen_editable(o):
                ui.warn(f"'{o.label}' is changed in the review (its own row) or the mode step.")
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
        # The last folder used, else no default: Enter on the whole user
        # profile would scan (and here rewrite records under) all of it.
        default_dir = cfg.last_input_dir or ""
        folder = _strip_surrounding_quotes(
            ui.ask_text("restamp.folder", "TIFF folder (the renamed one, or above it)",
                        default_dir))
        if not folder:
            ui.line("No folder given — nothing done.")
            return
        if not Path(folder).is_dir():
            self._print_error(f"Not a folder: {folder}")
            return
        last_mode = cfg.last_output_mode if cfg.last_origin_format == 'tiff' else None
        mode = ui.choose("restamp.mode", "Mode the masters were written with",
                         [(str(m), name) for m, name in (
                             (0, "Next to the TIFFs, this folder only (or an output folder)"),
                             (1, "A subfolder next to the TIFFs, this folder only"),
                             (2, "One flat output folder"),
                             (3, "A subfolder inside each TIFF folder"),
                             (4, "A renamed copy of each TIFF folder"),
                             (5, "A sibling folder of each TIFF folder"),
                             (6, "Below the export marker"),
                             (7, "Below the export marker, one subfolder"),
                             (8, "Next to the TIFFs, every subfolder"))],
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
                              "Export subfolder (Enter = the encoder's setting: "
                              f"{_child_setting('jxl_tiff_encoder', 'EXPORT_TIFF_SUBFOLDER', '') or 'all subfolders'})",
                              "").strip()
            if sub:
                cmd += ["--export-subfolder", sub]
        if mode in ("6", "7"):
            jf = ui.ask_text("restamp.jxl_folder",
                             "Masters' folder below the marker (Enter = the encoder's "
                             f"setting: {_child_setting('jxl_tiff_encoder', 'EXPORT_JXL_FOLDER', '16B_JXL')})",
                             "").strip()
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
        ui.line("A change applies to the next run of that script. Jobs started from "
                "this wizard send their own workers, effort, JPEG quality, export "
                "marker and TIFF -> JXL distance (Settings -> wrapper defaults, or the "
                "job's review): for those, the script's value counts when the script "
                "is run directly. Deletion, confirmations, guards and verification "
                "stay per-run choices.")
        while True:
            key = ui.choose("settings.script", "Which script's settings",
                            [(str(i + 1), label.replace("<->", "↔").replace("->", "→"))
                             for i, (_m, label) in enumerate(_OVERLAY_SCRIPTS)]
                            + [("0", "Back")], "0")
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
            numbered = {}
            table_rows = []
            for name in specs:
                script_value, help_text = literals.get(name, (getattr(mod, name, None), ""))
                if flt and flt not in f"{name} {help_text}".lower():
                    continue
                n = str(len(numbered) + 1)
                numbered[n] = name
                if name in section:
                    ok, _v = mod._overlay_value(specs[name], section[name])
                    shown = _setting_shown(section[name])
                    where = "changed here" if ok else "REFUSED by the script"
                else:
                    shown, where = _setting_shown(_overlay_json(script_value)), "script"
                table_rows.append((n, name, shown, where))
            _label = dict(_OVERLAY_SCRIPTS).get(module, module)
            ui.table(f"{_label.replace('<->', '↔').replace('->', '→')} — its settings"
                     + (f" (filter: {flt})" if flt else ""),
                     ["#", "Setting", "Value", "From"], table_rows)
            answer = ui.ask_text("settings.pick", "Number to change, /text to filter, "
                                 "Enter or 0 to go back", "").strip()
            if not answer or answer.lower() in ("0", "b"):
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
            _refresh_child_overlays()
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

        default_dir = self.config.config.last_input_dir or ""
        if self.ui.is_rich:
            console.print("\n[bold cyan]JPEG archives (jbrd): audit and repair[/bold cyan]")
            console.print(
                "[grey70]Checks the lossless JPEG -> JXL archives under a folder and "
                "repairs the ones that can no longer rebuild their JPEG; it also "
                "rewrites their EXIF so IrfanView shows it (image data untouched). "
                "Every repair works on a COPY and only replaces a file when the copy "
                "provably rebuilds the same JPEG — the cautious route is still to run "
                "it on a copy of the folder first. Why: v2.0.0-v2.0.3 wrote XMP "
                "markers that can break djxl --reconstruct_jpeg, and files before "
                "v2.19.2 kept their EXIF where IrfanView does not look.[/grey70]")
            # Explorer's "Copy as path" wraps the folder in double quotes;
            # every other wizard prompt strips them (_strip_surrounding_quotes)
            # and this one used to hand them to Path().is_dir() raw — "Folder
            # not found" for a folder that exists.
        else:
            print("\n--- JPEG archives (jbrd): audit and repair ---")
            print("Checks the lossless JPEG -> JXL archives under a folder and repairs")
            print("the ones that can no longer rebuild their JPEG; it also rewrites")
            print("their EXIF so IrfanView shows it (image data untouched). Every repair")
            print("works on a COPY and only replaces a file when the copy provably")
            print("rebuilds the same JPEG — the cautious route is still to run it on a")
            print("copy of the folder first. Why: v2.0.0-v2.0.3 wrote XMP markers that")
            print("can break djxl --reconstruct_jpeg; files before v2.19.2 kept their")
            print("EXIF where IrfanView does not look.")
        folder = _strip_surrounding_quotes(self.ui.ask_line(
            "jbrd.folder", "Folder to scan",
            f"Folder to scan [{default_dir}]: " if default_dir else "Folder to scan: ",
            default=default_dir or None))
        if not folder:
            self.ui.line("No folder given — nothing done.")
            return
        # Checked before the next question (rc7): a folder that does not exist
        # was refused only after "Audit only?"; a single .jxl, which
        # --repair-jbrd accepts, was refused as "Folder not found".
        _p = Path(folder)
        if not (_p.is_dir() or (_p.is_file() and _p.suffix.lower() == ".jxl")):
            self._print_error(f"Not a folder or a .jxl file: {folder}" if _p.exists()
                              else f"Folder not found: {folder}")
            return
        audit = self.ui.ask_yes("jbrd.audit", "Audit only (dry run, nothing written)?",
                                "Audit only (dry run, nothing written)? [Y/n]: ", True)

        cmd = [sys.executable, str(script), folder, '--repair-jbrd']
        if audit:
            cmd.append('--dry-run')
        rc = self._stream_child(cmd)
        if rc != 0:
            self._print_error("The audit reported problems (files still broken, an "
                              "archive that rebuilds another JPEG, or files it could not "
                              "read) — see the log above.")

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
        default_dir = self.config.config.last_input_dir or ""
        ui = self.ui
        if ui.is_rich:
            console.print("\n[bold cyan]Explorer thumbnails for existing JXLs[/bold cyan]")
            console.print(
                "[grey70]A small sRGB JPEG stored inside each file, which jxl-thumb shows "
                "in Windows Explorer without decoding the photo (most useful for "
                "lossless files). Nothing is re-encoded; each file keeps its "
                "modified date. JPEG->JXL lossless files (jbrd) are never touched. "
                "It also moves the EXIF where IrfanView reads it, in files written "
                "by v2.17.0 to v2.19.0.[/grey70]")
        else:
            print("\n--- Explorer thumbnails for existing JXLs ---")
            print("A small sRGB JPEG stored inside each file, which jxl-thumb shows in")
            print("Windows Explorer without decoding the photo. Nothing is re-encoded;")
            print("each file keeps its modified date. jbrd files are never touched.")
            print("It also moves the EXIF where IrfanView reads it, in files written")
            print("by v2.17.0 to v2.19.0.")
        folder = _strip_surrounding_quotes(ui.ask_line(
            "thumbs.folder", "Folder",
            f"Folder [{default_dir}]: " if default_dir else "Folder: ",
            default=default_dir or None))
        if not folder:
            ui.line("No folder given — nothing done.")
            return
        recursive = ui.ask_yes("thumbs.recursive", "Include subfolders?",
                               "Include subfolders? [Y/n]: ", True)
        policy = policies.get(ui.choose(
            "thumbs.policy", "Which files",
            [("1", "automatic (recommended): lossless files; every file when jxl-thumb "
                   "is installed"),
             ("2", "every file"), ("3", "remove them")], "1"), "auto")
        dry = ui.ask_yes("thumbs.dry", "Dry run first (only list what would change)?",
                         "Dry run first (only list what would change)? [y/N]: ", False)

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
        was_staging = current.staging_dir
        # Asked only where the tip means something: no thumbnail handler on
        # this PC, or the tip already hidden (so it can be turned back on).
        # Always asked on Windows (since v2.23.0): a reminder must be easy to
        # turn off, and back on.
        ask_tip = os.name == "nt"

        def _effective(default_value, last_value) -> str:
            """Flag a default that the saved session is currently overriding —
            otherwise editing it here looks like it did nothing, because the
            wizard and 'Repeat last workflow' both read the last_* value."""
            if last_value is None or last_value == default_value:
                return f"{default_value:g}" if isinstance(default_value, float) else f"{default_value}"
            shown = f"{default_value:g}" if isinstance(default_value, float) else f"{default_value}"
            was = f"{last_value:g}" if isinstance(last_value, float) else f"{last_value}"
            return f"{shown}  (last run used {was} — editing this adopts the new value)"

        ui = self.ui

        def _int_answer(text: str, keep: int) -> int:
            # Enter or anything but a number keeps the current value.
            text = (text or "").strip()
            return int(text) if text.isdigit() else keep

        # Rich shows the whole block first; the plain screen shows each
        # current value right above its question.
        _shown_distance = _effective(_sane_distance(current.default_distance),
                                     current.last_distance)
        if ui.is_rich:
            _yn = lambda v: "yes" if v else "no"
            ui.table("Wizard defaults", ["Setting", "Now"], [
                ("Staging folder", current.staging_dir or "system default"),
                ("Workers", _effective(current.default_workers, current.last_workers)),
                ("JPEG quality", _effective(current.default_quality, current.last_quality)),
                ("Effort", _effective(current.default_effort, current.last_effort)),
                ("Distance for TIFF → JXL", _shown_distance),
                ("Export marker", current.export_marker),
                ("Recommend jxl-thumb", _yn(current.show_thumbnail_tip)),
                ("Check for new releases", _yn(current.check_for_updates)),
            ], notes=["Enter keeps a value. These are the wizard's own defaults; the "
                      "scripts' settings are under Settings → 2."])
        else:
            ui.markup(None, "\n--- Edit Settings ---")

        ui.markup(None, f"Current staging: {current.staging_dir or 'system default'}")
        new_staging = ui.ask_line(
            "settings.staging", "Staging folder (Enter = keep, 'none' = system default)",
            "New staging (empty=keep, 'none'=system default): ",
            default=current.staging_dir or "")

        ui.markup(None, f"Current workers: {_effective(current.default_workers, current.last_workers)}")
        new_workers = _int_answer(ui.ask_line(
            "settings.workers", "Workers", "New workers: ",
            default=str(current.default_workers)), current.default_workers)

        ui.markup(None, f"Current quality: {_effective(current.default_quality, current.last_quality)}")
        new_quality = _int_answer(ui.ask_line(
            "settings.quality", "JPEG quality (1-100)", "New JPEG quality (1-100): ",
            default=str(current.default_quality)), current.default_quality)

        ui.markup(None, f"Current effort: {_effective(current.default_effort, current.last_effort)}")
        new_effort = _int_answer(ui.ask_line(
            "settings.effort", "Effort (1-10)", "New effort (1-10): ",
            default=str(current.default_effort)), current.default_effort)

        ui.markup(None, f"Current distance (TIFF->JXL): {_shown_distance}")
        # No default in the plain prompt: Enter there is "keep current".
        new_distance = ui.ask_line(
            "settings.distance", "Distance for TIFF → JXL (0 = lossless, 0.05 = finest lossy, 0.1 = near-lossless)",
            "New distance (0=lossless, 0.05=finest lossy, 0.1=near-lossless): ",
            default=(f"{_sane_distance(current.default_distance):g}" if ui.is_rich else None))

        # No "Confirm before delete?" since rc6 (Fable's second sweep): it was
        # asked and saved (ToolConfig.confirm_delete, kept for old configs) and
        # read by nothing — every delete asks for its HHMM token, and a "no"
        # here promised otherwise.

        ui.markup(None, f"Current marker: {current.export_marker}")
        new_marker = ui.ask_line("settings.marker", "Export marker", "New export marker: ",
                                 default=current.export_marker)
        new_tip = (ui.ask_yes("settings.thumbnail_tip",
                              "Recommend jxl-thumb (Explorer thumbnails) at start?",
                              "Recommend jxl-thumb (Explorer thumbnails) at start? (y/n): ",
                              current.show_thumbnail_tip)
                   if ask_tip else current.show_thumbnail_tip)
        new_update_check = ui.ask_yes(
            "settings.update_check", "Check once a day whether a new release is out?",
            "Check once a day whether a new release is out? (y/n): ",
            current.check_for_updates)

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
        self.config.config.show_thumbnail_tip = bool(new_tip)
        self.config.config.check_for_updates = bool(new_update_check)

        # Changing a default here is a statement about the NEXT run, so it also
        # adopts the saved session's value. Without this the wizard and 'Repeat
        # last workflow' keep reading last_* from an older session and the whole
        # settings screen appears to do nothing (the "repeat resets my workers"
        # complaint). Only values the user actually changed are propagated —
        # merely opening the screen and pressing Enter must not overwrite what
        # the last run used.
        cfg = self.config.config
        _changed = []
        if cfg.default_workers != was_workers:
            cfg.last_workers = cfg.default_workers
            _changed.append('last_workers')
        if cfg.default_quality != was_quality:
            cfg.last_quality = cfg.default_quality
            _changed.append('last_quality')
        if cfg.default_effort != was_effort:
            cfg.last_effort = cfg.default_effort
            _changed.append('last_effort')
        # The distance is the one of "Archive TIFFs" (TIFF -> JXL): it is the
        # last job's only when that job archived TIFFs, and only that task's
        # memory forgets its own. A JXL -> JXL job at d=1 was repeated at the
        # new TIFF default (rc6, Fable's second sweep D8).
        _distance_changed = _sane_distance(cfg.default_distance) != was_distance
        if _distance_changed and _task_of(cfg.last_origin_format, cfg.last_dest_format,
                                          cfg.last_conversion_type) == 'archive_tiff':
            cfg.last_distance = _sane_distance(cfg.default_distance)
        # The staging folder is the PC's (_new_workflow takes the last job's,
        # whatever its task): a new one here is the next job's too. The last
        # job's kept winning, and 'none' could not clear it (rc6, D9).
        if cfg.staging_dir != was_staging:
            cfg.last_staging = cfg.staging_dir
        # The tasks' own memories (#704) hold that value too: a changed
        # default is the next job's value for every task (review S6).
        if isinstance(cfg.task_memory, dict):
            for _tid, _entry in cfg.task_memory.items():
                if isinstance(_entry, dict):
                    for _field in _changed:
                        _entry.pop(_field, None)
                    if _distance_changed and _tid == 'archive_tiff':
                        _entry.pop('last_distance', None)

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

    def _task_memory(self, task):
        """What a new job of `task` (a TASKS id, or a workflow with one in
        '_task') starts from: the last run of THAT task (_TaskMemory,
        #704-#709). No task (a path that is not a new job) sees the config
        itself, as before."""
        if isinstance(task, dict):
            task = task.get('_task')
        c = self.config.config
        if not task or task not in _TASK_BY_ID:
            return c
        memory = c.task_memory if isinstance(c.task_memory, dict) else {}
        entry = memory.get(task)
        if (not memory and c.last_conversion_type not in _CONVERSIONS_OF_TWO_TASKS
                and _task_of(c.last_origin_format, c.last_dest_format,
                             c.last_conversion_type) == task):
            # A config saved before task_memory existed, whose last job WAS
            # this task: its values, as before (only another task's are not).
            # Only such a config (no memory at all: since rc5 every job saves
            # its task's), and only when the conversion names one task:
            # jxl_to_jpeg_auto is offered by "Export JPEGs" AND "Get the
            # original JPEGs back", and an auto export handed its JPG_web
            # folder, quality and forced decode to the first recovery of the
            # bit-exact originals (rc6, Fable's second sweep D7).
            return c
        return _TaskMemory(c, entry)

    def _new_workflow(self, task: Optional[str] = None) -> Dict[str, Any]:
        """A run's starting state: the last values of the same task (#704;
        with a config saved before then, the last job's when it was this task,
        else none), else the wrapper's settings, else the children's (the old
        wizard's defaults). The staging
        folder and the number of workers are the last job's, whatever its task:
        properties of the PC."""
        c = self._task_memory(task)
        _cfg = self.config.config
        last_staging = _cfg.last_staging if _cfg.last_staging is not None else c.staging_dir
        wf = {
            'input_dir': None, 'origin_format': None, 'dest_format': None,
            'conversion_type': None, 'mode': None,
            'workers': _cfg.last_workers or c.default_workers,
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
            'workers': 'last job' if _cfg.last_workers else 'settings',
            'effort': 'last job' if c.last_effort else 'settings',
            'quality': 'last job' if c.last_quality else 'settings',
            'staging': 'last job' if _cfg.last_staging is not None else 'settings',
            'use_ram': 'last job' if c.last_use_ram is not None else 'script',
            'compression': 'last job' if c.last_compression else 'script',
            'bit_depth': 'last job' if c.last_bit_depth else 'script',
            'add_preview': 'last job' if c.last_add_preview is not None else 'script',
            'overwrite_guard': 'last job' if c.last_overwrite_guard is not None else 'script',
            'd50_patch': 'script',
        }
        return wf

    def _job_pick_task(self, status: Dict[str, bool],
                       title: str = "New job — what do you want to do?") -> Optional[str]:
        """'job.task': what the run does. Each task is the (source, output,
        conversion) the old Steps 1-2 picked."""
        ui = self.ui
        keys = {}
        options = []
        for i, (tid, label, origin, dest, convs) in enumerate(TASKS, 1):
            why = self._task_unavailable(origin, dest, convs[0], status)
            options.append((str(i), f"{label:42} {origin.upper()} → {dest.upper()}"
                            + (f"   (needs {why})" if why else "")))
            keys[str(i)] = (tid, why)
        options.append(("0", "back"))
        while True:
            key = ui.choose("job.task", "Task", options, "1", title=title)
            if key == "0":
                return None
            tid, why = keys[key]
            if why:
                ui.warn(f"This task needs {why}, which this PC does not have.")
                continue
            return tid

    def _show_size_table(self, masters: bool) -> None:
        """Every measured distance with its typical size, before the user
        types one (masters: the TIFF and 8-bit rows first)."""
        def cells(sizes):
            return tuple(_size_text(s).rjust(6) for s in sizes)

        rows = []
        if masters:
            rows += [(name, *cells(sizes)) for name, sizes in _TYPICAL_TIFF_MB]
            rows.append(("",) * (len(_SIZE_COLUMNS) + 1))
        for d in sorted(_TYPICAL_SIZE_MB):
            label = _distance_label(d)
            if d == 10.0:
                label = "libjxl halves the resolution from here"
            name = f"JXL d={d:g}".ljust(4 + max(len(f"d={x:g}") for x in _TYPICAL_SIZE_MB)) \
                + (f" — {label}" if label else "")
            rows.append((name, *cells(_TYPICAL_SIZE_MB[d])))
        if masters:
            eight = {d: " / ".join(_size_text(x)[:-3] for x in v) + " MB"
                     for d, v in _TYPICAL_8BIT_MB.items()}
            notes = ["JXL sizes from a 16-bit TIFF. " + _SIZE_NOTE,
                     f"From an 8-bit TIFF: lossless {eight[0.0]} (about the size of "
                     f"d=0.05), d=0.05 {eight[0.05]}, lossy about the same from d=0.1 on."]
        else:
            notes = [_SIZE_NOTE_COPIES]
        self.ui.table("Typical file size", [""] + [c.rjust(6) for c in _SIZE_COLUMNS],
                      rows, notes=notes)

    def _ask_number(self, qid: str, prompt: str, default, lo, hi, kind=float):
        """A number from self.ui, clamped to [lo, hi]; the default on a typo."""
        text = self.ui.ask_text(qid, prompt, f"{default:g}" if isinstance(default, float)
                                else str(default))
        try:
            value = kind(text)
            # float() takes 'nan' and 'inf': clamped, NaN became `lo` — a
            # typed 'nan' distance was 0, LOSSLESS, without a word (rc6,
            # Fable's second sweep).
            if value != value or value in (float('inf'), float('-inf')):
                raise ValueError(text)
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
            _w = _label_width(_DISTANCE_CHOICES)
            opts = [(str(i), _choice_label(v, name, note, _w)) + _size_cells(v)
                    for i, (v, name, note) in enumerate(_DISTANCE_CHOICES, 1)]
            custom = str(len(opts) + 1)
            opts.append((custom, "your distance (0-25) — shows every distance's size")
                        + ("",) * len(_SIZE_COLUMNS))
            # Enter = the default distance of Settings (0.1 unless changed).
            default_key = next((k for k, (v, _n, _x) in zip([o[0] for o in opts],
                                                             _DISTANCE_CHOICES)
                                if v == preferred), custom)
            # The sizes are a 16-bit TIFF's (the title says so); the note gives
            # an 8-bit TIFF's lossless size, the one that differs, so 173 MB
            # does not scare someone who exports 8-bit TIFFs.
            key = ui.choose(
                "job.quality", "Quality", opts, default_key,
                note=f"  {_SIZE_NOTE}\n  {_SIZE_NOTE_8BIT}",
                columns=_SIZE_COLUMNS, columns_title=_SIZE_TITLE)
            if key != custom:
                workflow['distance'] = _DISTANCE_CHOICES[int(key) - 1][0]
            else:
                self._show_size_table(masters=True)
                last = self._task_memory(workflow).last_distance
                start = _sane_distance(last, fallback=preferred) if last is not None else preferred
                workflow['distance'] = self._ask_number("job.distance", "Distance (0-25, lower = better)",
                                                        start, 0.0, _MAX_DISTANCE)
            workflow['conversion_type'] = ('jxl_tiff_encoder_lossless'
                                           if workflow['distance'] == 0.0 and key != custom
                                           else 'jxl_tiff_encoder')
            frm['distance'] = 'you'
            _sd = _child_scan_distance()
            try:
                _differs = _sd != "same" and float(_sd) != float(workflow['distance'])
            except (TypeError, ValueError):
                _differs = False
            if _differs:
                _note = (f"Film scans use distance {_sd}"
                         f"{' (lossless)' if float(_sd) == 0 else ''} — scanner software, an IR "
                         f"page, or a TIFF with no Software tag and no camera EXIF (the run lists "
                         f"those folders). The review's Film scans row changes it.")
                ui.line()
                ui.markup(f"[grey70]  {_rich_safe(_note)}[/grey70]", f"  {_note}")
            _eb, _up = _child_eight_bit()
            _ed = _eight_bit_effective(_eb, _up, workflow['distance'])
            if _ed != float(workflow['distance']):
                _note = (f"8-bit TIFFs (not film scans) use distance {_ed:g}"
                         f"{' (lossless): an 8-bit TIFF is small lossless' if _ed == 0 else ''}"
                         f". The review's 8-bit TIFFs row changes it.")
                ui.line()
                ui.markup(f"[grey70]  {_rich_safe(_note)}[/grey70]", f"  {_note}")
        elif tid == 'derive_jxl':
            current = workflow.get('distance', _child_default('jxl_recompressor', 'distance'))
            _w = _label_width(_RECOMPRESS_CHOICES)
            opts = [(str(i), _choice_label(v, name, note, _w)) + _size_cells(v)
                    for i, (v, name, note) in enumerate(_RECOMPRESS_CHOICES, 1)]
            custom = str(len(opts) + 1)
            opts.append((custom, "your distance (0-25) — shows every distance's size")
                        + ("",) * len(_SIZE_COLUMNS))
            try:
                _cur = float(current)
            except (TypeError, ValueError):
                _cur = None
            # Enter = the recompressor's own setting (CJXL_DISTANCE, 1.0).
            default_key = next((str(i) for i, (v, _n, _x) in enumerate(_RECOMPRESS_CHOICES, 1)
                                if v == _cur), custom)
            key = ui.choose("job.quality", "Distance", opts, default_key,
                            note=f"  {_SIZE_NOTE_COPIES}",
                            columns=_SIZE_COLUMNS, columns_title=_SIZE_TITLE_COPIES)
            if key != custom:
                workflow['distance'] = _RECOMPRESS_CHOICES[int(key) - 1][0]
            else:
                self._show_size_table(masters=False)
                workflow['distance'] = self._ask_number(
                    "job.distance", "Target distance (0-25, lower = better)", current, 0.0, _MAX_DISTANCE)
            frm['distance'] = 'you'
        elif tid == 'reencode_jpeg':
            workflow['distance'] = self._ask_number(
                "job.distance", _DISTANCE_PROMPT,
                workflow.get('distance', 0.5), 0.0, _MAX_DISTANCE)
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
            adv['on_downgrade'] = _wizard_policy('ON_DOWNGRADE', 'copy')
            adv['on_regeneration'] = _wizard_policy('ON_REGENERATION', 'convert')
            if workflow.get('distance') and float(workflow['distance']) > 0:
                adv['on_scan'] = _wizard_policy('ON_SCAN', 'copy')
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
            _og_last = self._task_memory(workflow).last_overwrite_guard
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
        c = self._task_memory(workflow)         # the same task's last run (#709)
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
                    "review.distance", _DISTANCE_PROMPT, workflow['distance'], 0.0, _MAX_DISTANCE)
                if origin == 'tiff':
                    workflow['conversion_type'] = ('jxl_tiff_encoder_lossless'
                                                   if workflow['distance'] == 0.0
                                                   else 'jxl_tiff_encoder')
                if conv == 'jxl_recompress':
                    if workflow['distance'] > 0:
                        adv.setdefault('on_scan', _wizard_policy('ON_SCAN', 'copy'))
                    else:
                        adv.pop('on_scan', None)
                you('distance')
            rows.append(("distance", "Distance", _distance_shown(workflow['distance']), _distance))
            if origin == 'tiff' and dest == 'jxl':
                # Film scans keep their own distance (the encoder's
                # SCAN_DISTANCE, lossless by default): shown next to the
                # run's distance, so nobody wonders whether scans follow it.
                def _scan_distance():
                    current = adv.get('scan_distance') or _child_scan_distance()
                    typed = self.ui.ask_text(
                        "review.scan_distance",
                        "Film-scan distance (a number, or 'same' = the Distance above)",
                        str(current))
                    value = _valid_scan_distance(typed)
                    if value is None:
                        self._print_error(f"Not a distance: {typed!r} — kept {current}")
                        return
                    adv['scan_distance'] = value
                    you('scan_distance')
                _sd = adv.get('scan_distance') or _child_scan_distance()
                # Unless changed here, it is the encoder's SCAN_DISTANCE.
                frm.setdefault('scan_distance', 'script')
                _sd_shown = ("same as Distance" if _sd == "same" else _distance_shown(_sd))
                rows.append(("scan_distance", "Film scans", _sd_shown, _scan_distance))

                # 8-bit TIFFs that are not scans: the encoder's
                # EIGHT_BIT_DISTANCE (lossless at low distances by default).
                _eb_child, _eb_up = _child_eight_bit()

                def _eight_bit_distance():
                    current = adv.get('eight_bit_distance') or _eb_child
                    typed = self.ui.ask_text(
                        "review.eight_bit_distance",
                        f"8-bit TIFF distance ('auto' = lossless up to d={_eb_up:g}, "
                        f"'same' = the Distance above, or a number)", str(current))
                    value = _valid_eight_bit_distance(typed)
                    if value is None:
                        self._print_error(f"Not a distance: {typed!r} — kept {current}")
                        return
                    adv['eight_bit_distance'] = value
                    you('eight_bit_distance')
                _eb = adv.get('eight_bit_distance') or _eb_child
                frm.setdefault('eight_bit_distance', 'script')
                rows.append(("eight_bit_distance", "8-bit TIFFs", _eight_bit_shown(_eb, _eb_up),
                             _eight_bit_distance))
        # A JXL -> TIFF/JPEG/PNG decode encodes no JXL: the effort is unused.
        if not (origin == 'jxl' and dest in ('tiff', 'jpeg', 'png')):
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
                    ("on_scan", "Film scans (re-encode)",
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
            elif _jxl_run_replaces_in_place(
                    origin, dest, workflow['mode'], workflow.get('input_dir'),
                    (workflow.get('mode_config') or {}).get('output_dir'),
                    _mode_config_with_marker(workflow.get('mode_config'),
                                             _menu_marker(self)),
                    workflow.get('expert_flags')):
                # rc6's second review, N1: the gate refuses it (in place).
                rows.append(("derivative", "Colour/size derivative",
                             "not in place (the output folder, inside the input, "
                             "holds JXLs)", None))
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
            you('existing')
        rows.append(("existing", "Existing outputs",
                     "overwrite all" if workflow.get('overwrite_mode') == "1" else "sync (only newer)",
                     _existing))

        if delete_on:
            rows.append(("delete", "Delete the sources",
                         "YES — chosen with [D] in the mode step"
                         if (workflow.get('delete_source') or adv.get('delete_source'))
                         else "YES — --delete-source in the expert flags", None))
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
            rows.append(("delete", "Delete the sources", "no — to delete, go back [b] and pick [D]", None))
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
        message = None
        while True:
            rows = _review_order(self._review_rows(workflow, status))
            frm = workflow.get('_from') or {}
            head = []
            if workflow.get('mode') == 99:
                head.append(f"Manifest  {workflow.get('manifest_path')}  "
                            f"({len(workflow.get('manifest_entries') or [])} entries)")
            else:
                _folder = str(workflow.get('input_dir'))
                # A long path gets its own line instead of wrapping the label.
                head += (["Folder", f"  {_folder}"] if len(_folder) > 60
                         else [f"Folder    {_folder}"])
            head.append(f"Mode      {workflow.get('mode')} — "
                        f"{_mode_title(workflow.get('mode'), (workflow.get('mode_config') or {}).get('export_marker') or getattr(getattr(getattr(self, 'config', None), 'config', None), 'export_marker', None) or ToolConfig.export_marker)}")
            if (workflow.get('origin_format'), workflow.get('dest_format')) == ('tiff', 'jxl'):
                _mp = self._multipage_summary(workflow)
                if _mp:
                    head.append(f"Multi-page TIFFs: {_mp[0]}")
            numbered = {}
            table_rows = []
            for key, label, value, editor in rows:
                n = str(len(numbered) + 1)
                numbered[n] = (key, label, editor)
                src = frm.get(key) or "default"
                table_rows.append((n, label, value, src if editor else "fixed"))
            notes = list(self._summary_info(workflow))
            if any(r[0] == "scan_distance" for r in rows):
                notes.insert(0, "Film scans: scanner software, an IR page, or a TIFF "
                                "without camera EXIF.")
            notes.append("From: you · last job · settings (menu 9 → 1) · script (its own "
                         "setting) · default (the wizard's) · fixed (the mode step)")
            ui.table(f"Review — {title}", ["#", "Option", "Value", "From"], table_rows,
                     head=head, notes=notes)
            last = len(numbered)
            ui.actions([
                ("Change", [(f"1-{last}", "edit an option listed above"),
                            ("a", "show all options"), ("/text", "search options")]),
                ("See", [("c", "command line"), ("x", "expert flags")]),
                ("Run", [("r", "RUN"), ("d", "dry run first"), ("s", "save as preset")]),
                ("Leave", [("b", "back"), ("q", "cancel")]),
            ])
            if message:
                # Shown under the review, where it stays on screen (printed
                # before the redraw it scrolled away above the table).
                ui.panel(_rich_safe(message[1]), title=message[0], style="cyan",
                         plain=f"{message[0]}: {message[1]}")
                message = None
            answer = ui.ask_text("review.action",
                                 "Review — type a key, [r] to run "
                                 "(Enter shows this screen again)", "").strip()
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
                message = ("Command line",
                           "A manifest builds one command per row; the run prints each one."
                           if cmd is None else " ".join(_display_cmd(cmd)))
                continue
            if low == "x":
                flags = ui.ask_text("review.expert", "Expert flags (applied last; empty = none)",
                                    workflow.get('expert_flags') or "").strip()
                problem = _expert_flags_problem(workflow['origin_format'],
                                                workflow['dest_format'], flags)
                if problem:
                    message = ("Expert flags not changed", f"{problem}.")
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
        # The answers of an earlier job never ride along: the last job is THIS
        # run, so "Repeat last job" and a preset replay what it ran (#706, #707).
        # Read, not popped: the review saves at [s], [d] AND [r], and each save
        # is the same job — with the clearing above, a popped dict left the
        # [r] after a [d] without its [D] answers.
        for _field in _RUN_SCOPED_SESSION_FIELDS:
            setattr(config.config, _field, None)
        for _field, _value in (workflow.get('_pending_session_fields') or {}).items():
            setattr(config.config, _field, _value)
        adv = workflow.get('advanced_options', {}) or {}
        # The export marker a mode-6/7 (or manifest) job ran with, saved even
        # when it is the wrapper's: the mode step kept it only when it differed,
        # so a preset or "Repeat last job" ran with the marker menu 9 said ON
        # THE DAY OF THE REPLAY (rc6, Fable's second sweep D10; the owner's call:
        # a preset repeats what the job ran). The replay keeps it (_keep).
        _saved_mc = workflow.get('mode_config')
        if (str(workflow.get('mode')) in ('6', '7', '99')
                and not (_saved_mc or {}).get('export_marker')):
            _saved_mc = dict(_saved_mc or {}, export_marker=config.config.export_marker)
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
            mode_config=_saved_mc,
            manifest_path=workflow.get('manifest_path'),
            extra_options=workflow.get('extra_options') or {},
            exclude_folders=(workflow['exclude_folders']
                             if (origin, dest) in (('tiff', 'jxl'), ('jxl', 'tiff'))
                             and 'exclude_folders' in workflow
                             else config._STAGING_UNSET),
        )
        # What the next new job of this task starts from (#704-#709).
        _tid = workflow.get('_task') or _task_of(origin, dest, conv)
        if _tid in _TASK_BY_ID:
            memory = (dict(config.config.task_memory)
                      if isinstance(config.config.task_memory, dict) else {})
            memory[_tid] = _task_memory_entry(config.session_snapshot(), workflow,
                                              memory.get(_tid))
            config.config.task_memory = memory
            config.save_config()

    def new_job_flow(self, status: Dict[str, bool], manifest: bool = False) -> bool:
        """Menu 1 (and 4 with manifest=True): the task, the folder (or the
        manifest), the mode with its [A]/[M]/[D], the quality, then the
        review. [b] on the review goes back to the mode step."""
        import copy
        tid = self._job_pick_task(
            status, "Folder list (manifest) — which task do its rows run?" if manifest
            else "New job — what do you want to do?")
        if not tid:
            return False
        _tid, _label, origin, dest, convs = _TASK_BY_ID[tid]
        workflow = self._new_workflow(tid)
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
        input_dir = ui.ask_text("job.folder", "Folder with the files", default_dir)
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
                "job.exclude", "Folders to skip (names separated by ';', '-' = none; "
                "recursive modes only)", _excl_default or "-")
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
            console.print("\n[bold cyan]Auto Mode: Analyzing folder structure...[/bold cyan]")
            console.print("[grey70]This may take a moment for large folders...[/grey70]")
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
        # The report's own "====" rules and title become a panel.
        _body = [ln for ln in report.splitlines()
                 if ln.strip() and set(ln.strip()) != {"="} and ln.strip() != "FOLDER ANALYSIS"]
        self.ui.panel(_rich_safe("\n".join(_body)), title="[bold]Folder analysis[/bold]",
                      style="cyan", plain="\n".join(_body))

        if analysis['recommended_mode'] is None:
            self._print_error("Could not analyze folder structure.")
            return False

        rec_mode = analysis['recommended_mode']

        # Compute folder mappings for the recommended mode
        mappings = analyzer.compute_folder_mappings(analysis, rec_mode)

        # Build recommendation options
        mode_names = {m: _mode_title(m, export_marker) for m in range(9)}

        if mappings:
            _rows = []
            for src, dst, count in mappings[:10]:
                rel_src = Path(src).relative_to(input_dir) if _is_relative_to(Path(src), input_dir) else Path(src)
                rel_dst = Path(dst).relative_to(input_dir) if _is_relative_to(Path(dst), input_dir) else Path(dst)
                _rows.append((self._truncate_path(str(rel_src)),
                              self._truncate_path(str(rel_dst)), str(count)))
            self.ui.table(f"Mode {rec_mode} would write", ["From", "To", "Files"], _rows,
                          notes=([f"… and {len(mappings) - 10} more folder(s)"]
                                 if len(mappings) > 10 else []))
        else:
            self.ui.markup("[grey70]No folders to process with this mode.[/grey70]",
                           "No folders to process with this mode.")

        # Show menu and handle choice
        return self._wizard_auto_mode_menu(workflow, analyzer, analysis, rec_mode, mode_names, mappings)

    def _wizard_auto_mode_menu(self, workflow: Dict, analyzer: FolderAnalyzer, analysis: Dict, 
                               rec_mode: int, mode_names: Dict, mappings: List) -> bool:
        """Show the auto mode menu and handle user choice."""
        input_dir = Path(workflow['input_dir'])
        rec_name = mode_names.get(rec_mode, f"Mode {rec_mode}")
        
        ui = self.ui
        ui.table("What to do", ["Key", ""],
                 [("Y", f"Use mode {rec_mode} — {rec_name}"),
                  ("P", "Write a folder list (manifest CSV, editable in Excel)"),
                  ("V", "View that folder list"),
                  ("N", "Choose the mode myself")], show_header=False)
        # A typo in the plain prompt means Y (Rich asks again).
        choice = ui.ask_line("auto.what", "Select", "Select [Y/P/V/N]: ", default="Y",
                             choices=["Y", "P", "V", "N"]).upper()
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

            self.ui.markup(f"[green]✓ Using Mode {rec_mode} — {rec_name}[/green]", f"✓ Using Mode {rec_mode} — {rec_name}")

            return True

        elif choice == "P":
            # Generate manifest CSV
            manifest_path = self._generate_manifest(analyzer, analysis, rec_mode)
            if manifest_path:
                if RICH_AVAILABLE and console:
                    console.print(f"[green]✓ Manifest saved to:[/green] {_rich_safe(manifest_path)}")
                    console.print("[grey70]Edit in Excel, then run with [M] Run from manifest[/grey70]")
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
                self.ui.markup("[yellow]No manifest found. Generate one first with [P][/yellow]", "No manifest found. Generate one first with [P]")
                # No manifest - return to same menu so user can select [P]
                return self._wizard_auto_mode_menu(workflow, analyzer, analysis, rec_mode, mode_names, mappings)

        else:
            self.ui.markup("[grey70]Switching to manual mode selection...[/grey70]", "Switching to manual mode selection...")
            return self._wizard_select_mode_manual(workflow)

    def _wizard_auto_mode_post_manifest(self, workflow: Dict, analyzer: FolderAnalyzer, analysis: Dict, rec_mode: int, mode_names: Dict, mappings: List) -> bool:
        """Ask what to do after manifest was generated/viewed."""
        ui = self.ui
        ui.markup("")
        ui.markup("[bold]After manifest:[/bold]", "After manifest:")
        for key, rich_desc, plain_desc in (
                ("Y", f"Use Mode {rec_mode} — {mode_names.get(rec_mode, f'Mode {rec_mode}')}",
                 "Use this mode"),
                ("M", "Run from manifest", "Run from manifest"),
                ("N", "Choose mode manually", "Choose mode manually")):
            ui.markup(f"[{key}] {rich_desc}", f"[{key}] {plain_desc}")
        choice = ui.ask_line("auto.after_manifest", "Select", "Select [Y/M/N]: ", default="Y",
                             choices=["Y", "M", "N"]).upper()
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
                self.ui.markup(
                f"[yellow]No folder list (manifest CSV) in {_rich_safe(SCRIPT_DIR / 'manifests')}.[/yellow] "
                "Make one with a New job → [A] Auto Mode → [P], or write it by hand "
                "(docs/README_manifest.md).",
                f"No folder list (manifest CSV) in {SCRIPT_DIR / 'manifests'}. Make one with a "
                "New job -> [A] Auto Mode -> [P], or write it by hand (docs/README_manifest.md).")
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
            self.ui.markup("[yellow]No folders to add to manifest.[/yellow]", "No folders to add to manifest.")
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
        ui = self.ui
        ui.markup("[bold]Available manifests (newest first):[/bold]",
                  "Available manifests (newest first):")
        for i, m in enumerate(shown, 1):
            ui.markup(f"  [{i}] {_rich_safe(m.name)}", f"  [{i}] {m.name}")
        raw = ui.ask_line("manifest.pick", "Which manifest?", "Which manifest? [1]: ",
                          default="1", choices=[str(i) for i in range(1, len(shown) + 1)])
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
            try:
                all_rows = list(reader)
            except csv.Error as e:          # a cell over 128 KB (rc6, D16)
                self._print_error(
                    f"The manifest cannot be read as CSV (line {reader.line_num}: {e}): "
                    f"{manifest_path}")
                return
            first_row = all_rows[0] if all_rows else None
            rows = []
            if first_row is not None:
                if _is_manifest_header_row(first_row):
                    rows = all_rows[1:]
                else:
                    rows = all_rows
            for row in rows:
                if row and not (len(row) == 1 and row[0].strip().startswith('#')):
                    source = row[0].strip()
                    dest = row[1].strip() if len(row) > 1 else source
                    mode = row[2].strip() if len(row) > 2 else "?"
                    if not source.startswith('#'):
                        entries.append((source, dest, mode))

        if not entries:
            self.ui.markup("[yellow]Manifest is empty.[/yellow]", "Manifest is empty.")
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
            console.print(f"[grey70]Total: {len(entries)} entry(ies)[/grey70]")
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
            # Read whole here: a cell larger than the csv module's limit
            # (128 KB) raised csv.Error out of the wizard as a traceback (rc6,
            # Fable's second sweep D16).
            try:
                all_rows = list(reader)
            except csv.Error as e:
                self._print_error(
                    f"The manifest cannot be read as CSV (line {reader.line_num}: {e}): "
                    f"{manifest_path}")
                return None
            first_row = all_rows[0] if all_rows else None
            # Excel saves CSV with ';' between the columns in some languages:
            # the whole header (and every row) is then ONE cell, and each row's
            # Source a path that does not exist (D16's neighbour, Fable's p5).
            if (first_row is not None and len(first_row) == 1 and ";" in first_row[0]
                    and _is_manifest_header_row(first_row[0].split(";"))):
                self._print_error(
                    f"The manifest separates its columns with ';', not ','. Excel does "
                    f"that in some languages: re-open it and use 'Save As' -> 'CSV "
                    f"UTF-8 (comma delimited)', or replace the separators: {manifest_path}")
                return None
            # Only skip the first row if it really is the header; a manifest
            # whose header line was deleted must not silently lose entry #1.
            rows = []
            opt_index: Dict[str, int] = {}
            if first_row is not None:
                if _is_manifest_header_row(first_row):
                    rows = all_rows[1:]
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
                    rows = all_rows
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
            self.ui.markup(f"[grey70]{_rich_safe(note)}[/grey70]", note)

        if not entries:
            self.ui.markup("[yellow]Manifest is empty or only has comments.[/yellow]", "Manifest is empty or only has comments.")
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
            self.ui.markup(f"[yellow]{_rich_safe(warn)}[/yellow]", f"WARNING: {warn}")

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
            self.ui.markup(f"[grey70]{_rich_safe(note)}[/grey70]", note)

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
        if self.ui.is_rich:
            rows = []
            for i, (src, dst, mode) in enumerate(entries[:15], 1):
                row = [str(i), self._truncate_path(src), self._truncate_path(dst),
                       "auto" if mode is None else str(mode)]
                if _show_opts:
                    row.append(_summaries[i - 1] or "")
                rows.append(tuple(row))
            self.ui.table(f"Folder list — {Path(manifest_path).name}",
                          ["#", "Source", "Destination", "Mode"]
                          + (["Options"] if _show_opts else []), rows,
                          head=[f"File: {manifest_path}", f"{len(entries)} row(s)"],
                          notes=([f"… and {len(entries) - 15} more rows"]
                                 if len(entries) > 15 else []))

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
        return self.ui.ask_yes("manifest.proceed", "Run this folder list?",
                               "Run this folder list? [Y/n]: ", True)

    def _wizard_run_from_manifest(self, workflow: Dict) -> bool:
        """Step 4 alternative: run the workflow from a manifest CSV."""
        manifest_path = self._pick_manifest()
        if not manifest_path or not Path(manifest_path).exists():
            self.ui.markup(
                f"[yellow]No folder list (manifest CSV) in {_rich_safe(SCRIPT_DIR / 'manifests')}.[/yellow] "
                "Make one with a New job → [A] Auto Mode → [P], or write it by hand "
                "(docs/README_manifest.md).",
                f"No folder list (manifest CSV) in {SCRIPT_DIR / 'manifests'}. Make one with a "
                "New job -> [A] Auto Mode -> [P], or write it by hand (docs/README_manifest.md).")
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
            self.ui.panel(f"[bold red]{_q}[/bold red]", style="red", plain=_q)
            delete = self.ui.ask_yes("manifest.delete", "[bold red]Delete originals?[/bold red]",
                                     "Delete originals? [y/N]: ", False)
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
                self.ui.markup(f"[yellow]{console_msg}[/yellow]", console_msg)

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
            m0_desc = f"{origin.upper()} and {dest.upper()} side by side in the same folder"
            m8_desc = ("JXL next to each source file, all subfolders "
                       "(originals kept — use [D] to delete)")
        modes = [
            ("0", _mode_title(0), m0_desc),
            ("1", _mode_title(1), f"Creates a [green]'{m1_name}'[/green] subfolder"),
            ("2", _mode_title(2), "Every file from every subfolder in one folder (recursive)"),
            ("3", _mode_title(3), f"Creates [green]'{m3_name}'[/green] in each subfolder"),
            ("4", _mode_title(4), f"Replaces {origin.upper()} with {dest.upper()} in the folder name"),
            ("5", _mode_title(5), "Creates a sibling folder next to each source folder"),
            ("6", _mode_title(6, export_marker),
             f"ONLY the files INSIDE folders matching '{export_marker}' — nothing outside"),
            ("7", _mode_title(7, export_marker),
             "Like mode 6, but only one subfolder of the marker (asked next; empty = all = mode 6)"),
            ("8", _mode_title(8), m8_desc),
        ]

        ui = self.ui
        # [D] below is the DELETING entry and carries the red, or the warning
        # colour would point at the wrong option. But deletion is not the only
        # way this menu can lose a file: for a JXL->JXL run, modes 0 and 8
        # OVERWRITE the source in place (their descriptions carry the red "NOT
        # kept" warning) — the entries themselves stay green so [D] remains
        # the one red option.
        ui.table("Where the outputs go", ["Key", "Mode", "What it does"],
                 [("A", "[bold yellow]Auto Mode[/bold yellow]",
                   "analyses the folder and recommends a mode")]
                 + [(key, f"[bold green]{_rich_safe(name)}[/bold green]", desc.replace("\n", " "))
                    for key, name, desc in modes]
                 + [("M", "[bold]Folder list[/bold]", "run a manifest CSV made before"),
                    (self._DELETE_CHOICE, "[bold red]Convert and DELETE the originals ⚠️[/bold red]",
                     "pick a layout above, then the sources are removed once each output "
                     "is written and verified"),
                    ("?", "[bold]Details[/bold]", "each mode explained, with examples")],
                 notes=["The export marker (e.g. '_EXPORT'): Settings, menu 9 → 1. Other "
                        "folder names ('converted_jxl', '16B_TIFF'…): Settings, menu 9 → 2."],
                 markup=True)

        valid_choices = [m[0] for m in modes] + ["?", "A", "M", self._DELETE_CHOICE]
        while True:
            choice = ui.ask_line(
                "mode.pick", "Select mode",
                "Mode (0-8, D to delete originals, A for auto, M for manifest, ? for details): ",
                choices=valid_choices + ["a", "m", "d"]).upper()
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

        # (Where the marker and the folder names are set is the note under
        # the mode table; v2.23.0 no longer repeats it in a panel here.)

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
        self.ui.panel(f"[bold red]{_rich_safe(msg)}[/bold red]", style="red", plain=msg)
        try:
            go = self.ui.ask_yes(
                "delete.lossy_skipped",
                "[bold red]Delete already-converted originals anyway?[/bold red]",
                "Delete already-converted originals anyway? [y/N]: ", False)
        except (EOFError, KeyboardInterrupt):
            # A closed pipe (a scheduled run) declines: the originals are kept.
            print()
            go = False
        if not go:
            adv['delete_skipped'] = False
            note = "Already-converted originals will be KEPT."
            self.ui.markup(f"[yellow]{_rich_safe(note)}[/yellow]", note)
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
            # The child answers which files a run takes (_planned_sources: its
            # own finders, skips and modes 6/7 filter, a single file included):
            # no copy of its rules here (refactor C1).
            try:
                child = importlib.import_module(_child_module(origin, dest))
            except ImportError:
                child = None
            if root.is_file() and root.suffix.lower() not in exts:
                return 0       # not a file of the origin format: none to count
            if child is None:
                # No child to ask: the files of the origin format, flat or
                # recursive like the modes.
                if root.is_file():
                    return 1
                it = root.rglob('*') if mode in _RECURSIVE_MANIFEST_MODES else root.glob('*')
                return len([f for f in it if f.suffix.lower() in exts and f.is_file()])

            # The run's own command line, as the child parses it: the mode,
            # the transcoder's conversion flags (they decide its command),
            # the marker, the mode-7 subfolder (a different FILE SET, under
            # the panel whose visible count is what catches a wrong folder),
            # the modes 6/7 output folder (the recompressor's scans skip it),
            # the exclusion (#297's class) and the expert flags (#664) — all
            # of it from build_child_cmd, the builder of the run itself.
            try:
                cmd = self.build_child_cmd(
                    str(SCRIPT_DIR / f"{_child_module(origin, dest)}.py"), origin, dest,
                    str(root),
                    (workflow.get('mode_config') or {}).get('output_dir') if mode == 2 else None,
                    mode, workflow.get('workers') or 1, workflow,
                    workflow.get('advanced_options') or {}, manifest=False, quiet=True)
            except Exception:
                return -1      # a run the builder cannot describe yet: no count
            args = _child_plan_args(child, cmd)
            if args is None:
                return -1      # the child refuses this command line
            _was_disabled = child.logger.disabled
            child.logger.disabled = True        # its finders log; this is a preview
            try:
                with _with_child_marker(child, *_child_plan_globals(
                        args, _direction(origin, dest))):
                    # read_markers=False: the review redraws this count on
                    # every turn, and the recompressor's modes 6/7 would read
                    # the derivative markers of every JXL with exiftool each
                    # time, silently (#652, review V1). The count stays the one
                    # v2.29.1 showed: derivatives included. Files of the
                    # origin format, like the panel says (an auto transcoder
                    # run with no conversion type takes the other two too).
                    return sum(1 for f in child._planned_sources(args, read_markers=False)
                               if f.suffix.lower() in exts)
            except SystemExit:
                return -1      # the child refuses this input: no count to promise
            finally:
                child.logger.disabled = _was_disabled
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
        ui = self.ui

        # A lossy JXL -> JPEG never deletes its JXL (#600, T-B): say so at the
        # choice, not after both confirmations (the review refused it only
        # then). Only for the lossy conversion, which is settled by now; a PNG
        # may still become 16-bit later, so its rule stays at the review.
        if workflow.get('conversion_type') == 'jxl_to_jpeg_force':
            _never = _lossy_decode_never_deletes(workflow)
            if _never:
                _msg = (f"[D] is not available for this task: {_never}. Pick a layout "
                        f"— your JXLs are kept.")
                ui.panel(f"[yellow]{_rich_safe(_msg)}[/yellow]", style="yellow", plain=_msg)
                return self._wizard_select_mode(workflow)

        # --- Gate 1: undo a mis-key ---------------------------------------
        warn = (f"[D] deletes your original {origin.upper()} files after converting them. "
                f"This cannot be undone.")
        ui.panel(f"[bold red]{_rich_safe(warn)}[/bold red]", style="red", plain=warn)
        go = ui.ask_yes("delete.continue", "[bold]Continue?[/bold]", "Continue? [y/N]: ", False)
        if not go:
            ui.markup("[grey70]Cancelled — back to the mode list.[/grey70]")
            return self._wizard_select_mode(workflow)

        # --- The layout, same 0-8 as the normal path ----------------------
        ui.table("Where should the converted files go?", ["Key", "Mode"],
                 [(key, name) for key, name, _desc in modes])
        layouts = [m[0] for m in modes]
        layout = ""
        while layout not in layouts:
            layout = ui.ask_line("delete.layout", "Layout", "Layout [0]: ", default="0",
                                 choices=layouts)
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
        # rc8 (Fable's C1): what the child keeps whatever this panel says —
        # a JPEG archive (#745, #692) and, for a PNG, a master carrying its
        # TIFF's own tags (#734: most TIFF exports have an IPTC block).
        if origin == 'jxl' and dest == 'tiff':
            lines.append("Kept anyway: JPEG archives (JXLs made from a JPEG) — a TIFF "
                         "of their pixels is not the archive.")
        elif origin == 'jxl' and dest == 'png':
            lines.append("Kept anyway: JPEG archives, and masters that carry their TIFF's "
                         "own tags (IPTC, Photoshop data) — a PNG holds neither.")
        ui.panel("\n".join(lines), title="[bold red]About to delete originals[/bold red]",
                 style="red", plain="\n".join(lines))
        sure = ui.ask_yes("delete.sure", "[bold red]Is that what you want?[/bold red]",
                          "Is that what you want? [y/N]: ", False)
        if not sure:
            ui.markup("[grey70]Cancelled — back to the mode list.[/grey70]")
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
        ui.markup(f"[yellow]{_rich_safe(msg)}[/yellow]", msg)
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
            self.ui.markup(f"\n[grey70]{_msg}[/grey70]", f"\n{_msg}")
            verify = True
            workflow['verify_roundtrip'] = True
        elif (origin == 'tiff' and dest == 'jxl') or workflow.get('conversion_type') == 'jxl_recompress':
            vr_default = bool(self._task_memory(workflow).last_verify_roundtrip)
            explain = ("Decode each JXL and compare it with the source before deleting. "
                       "Lossless: the pixels must match exactly. Lossy: a brightness and "
                       "PSNR sanity check that catches a black or scrambled encode (not "
                       "quality loss — that is what you asked for). Roughly doubles the run.")
            self.ui.markup(f"\n[grey70]{explain}[/grey70]", f"\n{explain}")
            verify = self.ui.ask_yes(
                "delete.verify", "[cyan]Verify each output before deleting its source?[/cyan]",
                f"Verify each output before deleting its source? "
                f"[{'Y/n' if vr_default else 'y/N'}]: ", vr_default)
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

        ds_default = bool(self._task_memory(workflow).last_delete_skipped)
        ds_explain = (
            "Sources whose output already exists are reported as SKIP and are normally "
            "KEPT — which means a run interrupted between the conversion and the delete "
            "can never be finished without redoing everything. Turning this on deletes "
            "them too. They are never deleted on the timestamp alone: the output must "
            "exist and be structurally valid, " + _guarantee)
        self.ui.markup(f"\n[grey70]{ds_explain}[/grey70]", f"\n{ds_explain}")
        del_skipped = self.ui.ask_yes(
            "delete.skipped", "[cyan]Also delete originals that were already converted?[/cyan]",
            f"Also delete originals that were already converted? "
            f"[{'Y/n' if ds_default else 'y/N'}]: ", ds_default)

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
            pv_default = self._task_memory(workflow).last_provenance or pv_child
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
                    "\n  adopt   = for an archive made BEFORE this check existed, which has "
                    "no record at all. Each unrecorded output is VERIFIED against its source "
                    "and then stamped, so this is a ONE-TIME pass: afterwards the strict "
                    "check applies again.")
            _pv_list = "/".join(_pv_choices)
            self.ui.markup(f"\n[grey70]{pv_explain}[/grey70]", f"\n{pv_explain}")
            # A typo keeps the default (the plain prompt never asked again).
            _pi = self.ui.ask_line(
                "delete.provenance", "[cyan]Match existing outputs by[/cyan]",
                f"Match existing outputs by ({_pv_list}) [{pv_default}]: ",
                default=pv_default, choices=_pv_choices).lower()
            provenance = _pi if _pi in _pv_choices else pv_default
            workflow['provenance'] = provenance
            _staged['last_provenance'] = provenance
            _staged['last_adopt_scan'] = None
            if provenance == 'content':
                _n = ("Content matching reads every source file — expect the run to take "
                      "noticeably longer.")
                self.ui.markup(f"[yellow]{_rich_safe(_n)}[/yellow]", _n)
            elif provenance == 'adopt':
                # The scan is what makes adoption safe rather than a rubber
                # stamp, so it is ON unless the user turns it off knowingly.
                _sc = ("Adoption VERIFIES each unrecorded output against its source before "
                       "trusting it — it decodes every one, so on a large library this is "
                       "SLOW. Turning it off adopts the existing pairing without proof: only "
                       "do that if you know this archive was never built with a mode that "
                       "merges folders (2/4/5/6/7), which is what could have put a different "
                       "photo under that name.")
                self.ui.markup(f"\n[grey70]{_sc}[/grey70]", f"\n{_sc}")
                _scan = self.ui.ask_yes("delete.adopt_scan",
                                        "[cyan]Verify each one before adopting?[/cyan]",
                                        "Verify each one before adopting? [Y/n]: ", True)
                workflow['adopt_scan'] = bool(_scan)
                _staged['last_adopt_scan'] = bool(_scan)
                if not _scan:
                    _w = ("Adopting WITHOUT verification. Every existing output will be "
                          "trusted as belonging to the source that shares its name.")
                    self.ui.markup(f"[yellow]{_rich_safe(_w)}[/yellow]", _w)
        if del_skipped and not verify and not _provable:
            _w = ("Deleting already-converted originals with no content check: what "
                  "stands between a stale output and your original is the provenance "
                  "marker (it names the source FILE, not its pixels) and a structural "
                  "check.")
            self.ui.markup(f"[yellow]{_rich_safe(_w)}[/yellow]", _w)

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
            ("0", _mode_title(0), m0_desc),
            ("1", _mode_title(1), f"Creates a '{m1_name}' subfolder"),
            ("2", _mode_title(2), "Every file in one folder (recursive)"),
            ("3", _mode_title(3), f"'{m3_name}' in each subfolder"),
            ("4", _mode_title(4), f"Renames the folder {origin.upper()} → {dest.upper()}"),
            ("5", _mode_title(5), "A sibling folder next to each source folder"),
            ("6", _mode_title(6, export_marker), f"Only inside folders matching '{export_marker}'"),
            ("7", _mode_title(7, export_marker), f"Only one subfolder of '{export_marker}'"),
            ("8", _mode_title(8), m8_desc),
        ]

        ui = self.ui
        ui.table("Choose the mode", ["Key", "Mode", "What it does"],
                 [(k, f"[bold green]{_rich_safe(n)}[/bold green]", d) for k, n, d in modes]
                 + [(self._DELETE_CHOICE, "[bold red]Convert and DELETE the originals[/bold red]",
                     "pick a layout, then the sources are removed once each output is "
                     "written and verified")],
                 markup=True)
        valid_choices = [m[0] for m in modes] + [self._DELETE_CHOICE]
        choice = ""
        while choice not in valid_choices:
            choice = ui.ask_line("mode.manual", "Select mode",
                                 f"Select ({'/'.join(valid_choices)}): ",
                                 choices=valid_choices + [self._DELETE_CHOICE.lower()]).upper()

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
                "Single file → same folder; folder → every file replaced in place.\n"
                "[bold green]Non-recursive[/bold green] - subfolders are NOT processed.")
            inplace_rec_desc = (
                "Like mode 0, RECURSIVE: walks every subfolder and REPLACES each\n"
                "source JXL with its recompressed version, in place. The original\n"
                "bytes cannot be recovered afterwards.")
        else:
            inplace_desc = (
                f"{origin.upper()} and {dest.upper()} stay side by side in the SAME folder.\n"
                f"Input file/folder determines output location.\n"
                f"Single file → same folder; folder → flat output in that folder.\n"
                f"[bold green]Non-recursive[/bold green] - subfolders are NOT processed.")
            inplace_rec_desc = (
                "Like mode 0, but RECURSIVE: walks every subfolder and writes each output\n"
                "next to its source.\n"
                "[bold green]Your originals are KEPT.[/bold green] Deleting them is [bold red][D][/bold red], "
                "below — it is not a mode.")

        details = [
            ("0", _mode_title(0), inplace_desc),

            ("1", _mode_title(1),
             f"Creates a [green]'{_dest_folder_names(origin, dest)[0]}'[/green] subfolder next to each source folder.\n"
             f"Example: [cyan]F:/Photos/2024/[/cyan] → [cyan]F:/Photos/2024/{_dest_folder_names(origin, dest)[0]}/[/cyan]\n"
             f"Works on folder input only. Non-recursive."),

            ("2", _mode_title(2),
             f"All {origin.upper()} files from ALL subfolders are merged into a single output folder.\n"
             f"Fully recursive — every subfolder is scanned.\n"
             f"If you specify an output root, files land there.\n"
             f"Otherwise uses the input root as output root."),

            ("3", _mode_title(3),
             f"Each subfolder gets its own [green]'{_dest_folder_names(origin, dest)[1]}'[/green] subfolder.\n"
             f"Preserves folder structure: [cyan]F:/Photos/2024/A/[/cyan] → [cyan]F:/Photos/2024/A/{_dest_folder_names(origin, dest)[1]}/[/cyan]"),

            ("4", _mode_title(4),
             f"Renames the folder, replacing [green]{origin.upper()}[/green] with [green]{dest.upper()}[/green] in the folder name.\n"
             f"[cyan]F:/Photos/{origin.upper()}_raw/[/cyan] → [cyan]F:/Photos/{dest.upper()}_raw/[/cyan]\n"
             f"If {origin.upper()} is not found, appends {dest.upper()} and logs a warning."),

            ("5", _mode_title(5),
             "Creates a sibling folder next to each source folder, without renaming it.\n"
             "[cyan]F:/Photos/Raw/[/cyan] → [cyan]F:/Photos/JXL_16bits/[/cyan] (or TIFF_16bits / JXL_jpeg / JPEG_recovered, by direction)"),

            ("6", _mode_title(6, export_marker),
             f"ONLY processes files INSIDE folders matching '{export_marker}' (prefix/suffix token).\n"
             f"Recursively finds ALL matching folders and processes everything under each.\n"
             f"Ignores ALL files outside matching folders.\n"
             f"Works with _EXPORT, Export_Lightroom, Lightroom_Export, etc. (case-insensitive)."),

            ("7", _mode_title(7, export_marker),
             "Like mode 6, but ONLY processes files inside one subfolder of the export marker.\n"
             "The subfolder name is asked next (empty = all subfolders = same as mode 6).\n"
             "Files in other subfolders within export folders are ignored.\n"
             "Use when you keep different colour-space variants in separate subfolders."),

            ("8", _mode_title(8), inplace_rec_desc),

            ("D", "Convert and DELETE the originals",
             f"[bold red]IRREVERSIBLE.[/bold red] Not a layout: you pick any mode 0-8 above, and the\n"
             f"original {origin.upper()} files are removed once each output has been written to\n"
             f"that mode's destination and verified.\n"
             f"You are asked to confirm, then shown exactly how many files from which\n"
             f"folder, then asked for the current time (HHMM). A dry run never deletes."),
        ]

        ui = self.ui
        ui.section("Each mode in detail")
        for key, name, desc in details:
            # Red marks what actually destroys data — which is [D], not mode 8.
            # Mode 8 keeps your files.
            style = "red" if key == self._DELETE_CHOICE else "blue"
            ui.panel(desc, title=f"[bold {style}][{key}] {_rich_safe(name)}[/bold {style}]",
                     style=style, plain=f"[{key}] {name}\n   {_strip_styles(desc)}\n")
        valid_choices = [d[0] for d in details] + ["A", "M"]
        choice = ""
        while choice not in valid_choices:
            choice = ui.ask_line("mode.details", "Select mode", "Select mode [0-8/D/A/M]: ",
                                 choices=valid_choices + ["a", "m",
                                                          self._DELETE_CHOICE.lower()]).upper()

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
        """Mode settings: Mode-specific configuration"""
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
                console.print("\n[bold cyan]Mode settings: Manifest Mode[/bold cyan]")
                console.print(f"[grey70]Running {len(workflow.get('manifest_entries', []))} entries from manifest[/grey70]")
            else:
                print("\n--- Mode settings: Manifest Mode ---")
            workflow['mode_config'] = mode_config
            return True

        if mode in [6, 7]:
            current = self.config.config.export_marker
            ui = self.ui
            ui.markup("\n[bold cyan]Mode settings: Marker Configuration[/bold cyan]",
                      "\n--- Mode settings: Marker Configuration ---")
            new_marker = ui.ask_line("mode.cfg.marker",
                                     "Export marker (the folder name modes 6/7 look for)",
                                     f"Export marker (the folder name modes 6/7 look for) [{current}]: ",
                                     default=current)

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
                    (self._task_memory(workflow).last_mode_config or {}).get('export_subfolder', '')
                sub = self.ui.ask_line(
                    "mode.cfg.subfolder",
                    "Subfolder inside export marker to process (empty = all, same as mode 6)",
                    f"Subfolder inside export marker (empty = all) [{last_sub}]: ",
                    default=last_sub)
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

            # The modes 6/7 output folder. Only the directions whose child
            # accepts --export-jxl-folder / --export-jpeg-folder offer it; the
            # child validates the name itself (one plain component, not the
            # marker, not the input subfolder) and exits 2 with a clear
            # message the wrapper shows.
            if _export_folder_settable(origin, dest):
                _script_default = _export_folder_name(origin, dest)
                # The same task's last folder (#705: copies made in sRGB_JXL
                # sent the next masters there).
                _folder_default = (mode_config.get('export_jxl_folder')
                                   or (self._task_memory(workflow).last_mode_config
                                       or {}).get('export_jxl_folder')
                                   or _script_default)
                _hint = (" (e.g. JPG_web — the script's own name is the recovery's)"
                         if origin == 'jxl' and dest in ('jpeg', 'png')
                         and _folder_default == _script_default else "")
                _folder = self.ui.ask_line(
                    "mode.cfg.jxl_folder", f"Output folder name under the marker{_hint}",
                    f"Output folder name under the marker{_hint} [{_folder_default}]: ",
                    default=_folder_default)
                _folder = _strip_surrounding_quotes(_folder).strip().strip('/\\')
                if _folder == _script_default:
                    mode_config.pop('export_jxl_folder', None)
                else:
                    mode_config['export_jxl_folder'] = _folder

        elif mode == 2:
            default_out = Path(workflow['input_dir']).parent / "output"
            self.ui.markup("\n[bold cyan]Mode settings: Flat Folder[/bold cyan]",
                           "\n--- Mode settings: Flat Folder ---")
            output_dir = self.ui.ask_line("mode.cfg.output_dir", "Output directory",
                                          f"Destination [{default_out}]: ",
                                          default=str(default_out))
            mode_config['output_dir'] = _strip_surrounding_quotes(output_dir)

        elif mode == 1:
            folder_name = _dest_folder_names(origin, dest)[0]
            self.ui.markup("\n[bold cyan]Mode settings: Subfolder Name[/bold cyan]",
                           "\n--- Mode settings: Subfolder Name ---")
            self.ui.markup(f"Will create: [green]'{_rich_safe(folder_name)}'[/green] in each source folder",
                           f"Will create: '{folder_name}' in each source folder")

        elif mode == 3:
            folder_name = _dest_folder_names(origin, dest)[1]
            self.ui.markup("\n[bold cyan]Mode settings: Subfolder Name[/bold cyan]",
                           "\n--- Mode settings: Subfolder Name ---")
            self.ui.markup(f"Will create: [green]'{_rich_safe(folder_name)}'[/green] in each source folder",
                           f"Will create: '{folder_name}' in each source folder")

        elif mode in [4, 5]:
            self.ui.markup("\n[bold cyan]Mode settings: Rename Configuration[/bold cyan]",
                           "\n--- Mode settings: Rename Configuration ---")
            self.ui.markup(f"Example: folder_{origin} → [green]folder_{dest}[/green]")

        else:
            self.ui.markup("\n[bold cyan]Mode settings: Confirmation[/bold cyan]",
                           "\n--- Mode settings: Confirmation ---")
            self.ui.markup(f"Mode {mode} - OK")

        workflow['mode_config'] = mode_config
        return True

    def _confirm_hhmm(self, headline: str, body: str) -> bool:
        """The HHMM proof-of-presence prompt, shared by every destructive gate."""
        from datetime import datetime
        now = datetime.now()
        token = now.strftime("%H%M")

        ui = self.ui
        ui.markup(f"[bold red]{headline}[/bold red]", f"\n{headline}")
        ui.markup(f"[red]{body}[/red]", f"⚠️  {body}")
        ui.markup(f"Enter current time ({token}) to confirm:", f"Enter {token} to confirm:")
        user_input = ui.ask_raw("gate.hhmm", "> ")

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
            self.ui.markup(f"[yellow]{_rich_safe(warn)}[/yellow]", f"WARNING: {warn}")
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
        # anything is charged or written; then the mode-7 subfolder step.
        if _job_gates(self, workflow, "manifest", origin=origin, dest=dest,
                      advanced=advanced, row_effs=_row_effs,
                      resolved_entries=resolved_entries,
                      row_opts_all=_row_opts_all, row_markers=_row_markers):
            return False

        # The mode-7 derivation above may have filled the run's export_subfolder.
        # Rows with their own Export* columns hold a COPY of mode_config, so
        # they would not see it — rebuild, so every row inherits the derived
        # subfolder unless it has its own. (_row_export can only be computed
        # here, for the same reason.)
        _row_effs = [_row_effective_options(workflow, advanced, _o, origin, dest)
                     for _o in _row_opts_all]
        _row_export = [_row_export_cfg(_w) for _w, _a in _row_effs]

        # Each row's resolved output folders, filled by the collision scan
        # for the output-feeds-Source step after it.
        cross_out_folders: Dict[int, Set[str]] = {}
        # The output folder names, the overlapping Sources, the output
        # collisions and an output that feeds another row's Source
        # (_GATE_RULES, path "manifest_outputs", in that order).
        if _job_gates(self, workflow, "manifest_outputs", origin=origin, dest=dest,
                      resolved_entries=resolved_entries, row_export=_row_export,
                      row_effs=_row_effs, analyzer=analyzer, marker=_marker,
                      cross_out_folders=cross_out_folders):
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
        # already points at.) Each row's options are a COPY of `advanced`
        # made above (_row_effective_options), and the cmd builder reads the
        # copies: they are made again after the answer, or a "No" was ignored
        # and every row was sent --delete-skipped (rc6, Fable's second sweep D1).
        self._confirm_lossy_delete_skipped(workflow)
        _row_effs = [_row_effective_options(workflow, advanced, _o, origin, dest)
                     for _o in _row_opts_all]

        # JXL -> JXL in-place entries (mode 0/8, no Destination) REPLACE the
        # source files — the cmd builder passes --delete-confirm-off for
        # those, so the wrapper MUST charge the HHMM token itself, exactly
        # like the delete case. Decided ONCE per row, here, and handed to
        # each row's command (`_in_place_planned`): the confirmation below and
        # the flag must never disagree (rc6's second review, S1).
        _row_in_place = [origin == 'jxl' and dest == 'jxl'
                         and _recompress_entry_in_place(
                             _s, dest_path, detected_mode,
                             dict(zip(('export_marker', 'export_subfolder',
                                       'export_jxl_folder'), _row_export_cfg(_wr))),
                             _wr.get('expert_flags'))
                         for (_s, dest_path, detected_mode), (_wr, _ar)
                         in zip(resolved_entries, _row_effs)]
        for (_wr, _ar), _ip in zip(_row_effs, _row_in_place):
            _wr['_in_place_planned'] = bool(_ip)
        _any_in_place_recompress = any(_row_in_place)

        if _job_gates(self, workflow, "manifest_in_place",
                      any_in_place_recompress=_any_in_place_recompress, dry_run=dry_run):
            return False

        # [D] on a manifest whose every row loses its delete (each one a lossy
        # decode or a derivative: _policy_delete takes it out) deletes nothing,
        # and the token's "will be DELETED" was asked all the same (rc6,
        # Fable's second sweep). The rows' own command lines say.
        _rows_delete = bool(advanced.get('delete_source')) and any(
            '--delete-source' in (self.build_child_cmd(
                script, origin, dest, _s, _d, _m, workers, _wr, _ar,
                manifest=True, quiet=True) or [])
            for (_s, _d, _m), (_wr, _ar) in zip(resolved_entries, _row_effs))
        if not dry_run and (_flags_request_delete(workflow.get('expert_flags'), origin, dest)
                            or _rows_delete
                            or _any_in_place_recompress):
            if _any_in_place_recompress and not (
                    _flags_request_delete(workflow.get('expert_flags'), origin, dest)
                    or _rows_delete):
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
            console.print(f"[grey70]Script: {_rich_safe(script)} | Workers: {workers}[/grey70]")
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
            self.ui.markup(f"[{i}/{total_entries}] [bold]{detected_mode}[/bold] | {_rich_safe(self._truncate_path(source, 40))} → {_rich_safe(self._truncate_path(dest_path, 40))}", f"[{i}/{total_entries}] Mode {detected_mode} | {source} → {dest_path}")

            # A Source that is not there (a drive not connected, a folder
            # renamed) is this entry's error, not the run's: the child
            # refused it as a usage error (exit 2), and the manifest stopped
            # every other entry with a message calling it a wrapper bug (rc6,
            # Fable's second sweep D14; the owner's call: skip it, run the
            # others, end with an error).
            if not Path(source).exists():
                error_count += 1
                self._print_error(f"Source not found: {source} — this entry is "
                                  f"skipped, the others run.")
                entry_reports.append({
                    "index": i, "mode": detected_mode, "source": source,
                    "state": "source not found", "summary": None,
                })
                continue

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
                # Ctrl+C: the child ended by itself after its files in progress
                # (C4; exit 130 after its summary, or 0 when it was already done),
                # or _stream_child killed it (a second press, an old child).
                # Record
                # the entry, mark the rest not started and fall through to the
                # summary — a manifest runs for hours, and the accounting of
                # what DID complete must survive the interruption. main() then
                # reports the cancellation and exits 130.
                _rc_done = getattr(self, "_last_child_rc", None)
                if _rc_done == 0:
                    ok_count += 1
                else:
                    error_count += 1
                entry_reports.append({
                    "index": i, "mode": detected_mode, "source": source,
                    "state": ("ok" if _rc_done == 0 else
                              "interrupted" if _rc_done == 130 else "cancelled"),
                    "summary": self._last_child_summary,
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
                self.ui.markup("  [yellow]Cancelled by user[/yellow]", "  Cancelled by user")
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

        # A cancelled entry (exit 3: a child's own question answered "no" — or
        # not answered at all, stdin closed) did not do its work. Nobody saw it
        # in an unattended run (a preset, a scheduled task), whose exit code
        # said it went well (rc6, Fable's second sweep D13).
        if skip_count and workflow.get('unattended'):
            return False
        return error_count == 0

    def _manifest_step_mode7_subfolder(self, workflow, resolved_entries, _row_opts_all,
            _row_markers) -> bool:
        """A mode-7 row whose subfolder cannot be told from its Source: refused
        unattended, asked otherwise. One subfolder named by every mode-7 Source
        becomes the run's export_subfolder (workflow['mode_config']). False =
        refused (the step printed why)."""
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
                self.ui.markup(f"[yellow]{_rich_safe(_sub_warn)}[/yellow]", f"WARNING: {_sub_warn}")
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
                ok_sub = self.ui.ask_yes("manifest.run_anyway_subfolder", "Run anyway?", "Run anyway? [y/N]: ", False)
                if not ok_sub:
                    return False
            elif len(derived) == 1:
                _sub = derived.pop()
                workflow.setdefault('mode_config', {})['export_subfolder'] = _sub
                _sub_msg = (f"Mode-7 entries: processing only subfolder {_sub!r} "
                            f"(derived from the manifest's Source paths).")
                self.ui.markup(f"[grey70]{_rich_safe(_sub_msg)}[/grey70]", _sub_msg)
        return True

    def _manifest_step_output_folder_names(self, origin, dest, resolved_entries,
            _row_export) -> bool:
        """Each mode 6/7 row's output folder name, as its child validates it at
        startup. False = refused (the step printed why)."""
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
        return True

    def _manifest_step_in_place_unattended(self, workflow, any_in_place_recompress,
                                           dry_run) -> bool:
        """A manifest that recompresses JXLs in place (mode 0/8, no
        Destination) is refused unattended: nobody can type the HHMM token of
        an in-place replacement in a scheduled run. False = refused (the step
        printed why)."""
        # The _run_saved_session gate keys on last_mode (0/8) and misses
        # manifest presets, which store 99 — so the refusal has to happen here,
        # where the resolved entries (and their real modes) are known.
        if any_in_place_recompress and workflow.get('unattended') and not dry_run:
            self._print_error(
                "Refusing to run unattended: this manifest recompresses JXLs in "
                "place (mode 0/8, or a mode-2 Destination inside its Source that "
                "already holds JXLs), and the HHMM confirmation cannot be given "
                "here. Run it from the menu, or add --dry-run to simulate it.")
            return False
        return True

    def _manifest_step_source_overlaps(self, workflow, resolved_entries) -> bool:
        """Two rows whose Sources overlap: refused unattended, asked otherwise.
        False = refused (the step printed why)."""
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
            self.ui.markup(f"[yellow]{_rich_safe(head)}[/yellow]", f"WARNING: {head}")
            for a, b in overlaps[:10]:
                print(f"  {a}  <->  {b}")
            if workflow.get('unattended'):
                self._print_error(
                    "Refusing to run unattended: split the manifest so each folder "
                    "is covered by exactly one entry, or run it from the menu.")
                return False
            ok_overlap = self.ui.ask_yes("manifest.run_anyway_overlap", "Run anyway?", "Run anyway? [y/N]: ", False)
            if not ok_overlap:
                return False
        return True

    def _manifest_step_output_collisions(self, workflow, origin, dest, resolved_entries,
            analyzer, _marker, _row_export, _row_effs, cross_out_folders) -> bool:
        """Two rows writing the same output file: refused. Fills cross_out_folders
        (each row's resolved output folders) for the next step. False =
        refused (the step printed why)."""
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
        # Each entry's marker is the one its run parses (its ExportMarker
        # column, the run's, or --export-marker in the expert flags: #708, the
        # columns alone said "cannot collide" and two rows wrote one file), and
        # the check reasons per row too: disjoint trees under markers that
        # cannot nest stay skippable, while nested marker dirs force the scan.
        _plan_markers = self._row_plan_markers(origin, dest, resolved_entries, _row_effs)
        _can_skip = not self._manifest_needs_collision_scan(
            resolved_entries, _marker,
            row_markers=[_pm or _m for _pm, (_m, _s, _j)
                         in zip(_plan_markers, _row_export)])
        if _can_skip:
            _skip_msg = ("Collision check: skipped (no two entries can share an "
                         "output folder).")
            self.ui.markup(f"[grey70]{_rich_safe(_skip_msg)}[/grey70]", _skip_msg)
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
                row_effs=_row_effs,
            )
        if collisions:
            self._print_error(
                "Aborting: manifest entries would write different files to the same output."
            )
            for a, b, d in collisions[:10]:
                msg = f"  {a} + {b}  ->  same name in {d}"
                self.ui.markup(f"[red]{_rich_safe(msg)}[/red]", msg)
            if len(collisions) > 10:
                print(f"  ... and {len(collisions) - 10} more")
            self._print_error(
                "Rename the inputs, pick another mode, or run the folder directly "
                "(a non-manifest run detects this itself)."
            )
            return False
        return True

    def _manifest_step_output_feeds_source(self, workflow, resolved_entries,
            cross_out_folders) -> bool:
        """One row's output folder is another row's Source: refused unattended,
        asked otherwise. False = refused (the step printed why)."""
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
            self.ui.markup(f"[yellow]{_rich_safe(head)}[/yellow]", f"WARNING: {head}")
            for src_a, src_b, out in cross_collisions[:10]:
                msg = (f"  output of [{self._truncate_path(src_a, 30)}] -> "
                       f"{self._truncate_path(out, 30)}  ==  Source of "
                       f"[{self._truncate_path(src_b, 30)}]")
                self.ui.markup(f"[yellow]{_rich_safe(msg)}[/yellow]", msg)
            if len(cross_collisions) > 10:
                print(f"  ... and {len(cross_collisions) - 10} more")
            if workflow.get('unattended'):
                self._print_error(
                    "Refusing to run unattended: this manifest reads folders that "
                    "another entry of the same run writes. Run it from the menu, or "
                    "split it into separate runs.")
                return False
            ok_cross = self.ui.ask_yes("manifest.run_anyway_cross", "Run anyway?", "Run anyway? [y/N]: ", False)
            if not ok_cross:
                return False
        return True

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
                console.print("[grey70]Combined log:[/grey70]")
                console.print(f"[grey70]  {_escape(str(log_path))}[/grey70]", soft_wrap=True)
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
        _del = extras_total.pop(_common.SUMMARY_DELETED, 0)
        _del_arch = extras_total.pop(_common.SUMMARY_DELETED_ARCHIVED, 0)
        _kept = extras_total.pop(_common.SUMMARY_KEPT, 0)
        _refused = extras_total.pop(_common.SUMMARY_REFUSED, 0)
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

    def _row_plan_markers(self, origin, dest, resolved_entries, row_effs) -> List[Optional[str]]:
        """The export marker each manifest row's run parses: the child's own
        parse (_child_plan_args) of the argv build_child_cmd gives that row,
        as the collision walk reads it (_child_plan_globals) — the expert
        flags included (#708). None where the child cannot be imported or
        refuses the command line: the row's columns decide, as before. No
        file is read."""
        try:
            child = importlib.import_module(_child_module(origin, dest))
        except Exception:
            return [None] * len(resolved_entries)
        markers: List[Optional[str]] = []
        for i, (source, dest_path, mode) in enumerate(resolved_entries):
            marker = None
            if mode is not None and row_effs is not None and i < len(row_effs):
                wf_row, adv_row = row_effs[i]
                try:
                    ns = _child_plan_args(child, self.build_child_cmd(
                        str(SCRIPT_DIR / f"{child.__name__}.py"), origin, dest,
                        str(source), dest_path, mode, 1, wf_row, adv_row,
                        manifest=True, quiet=True))
                except Exception:
                    ns = None
                if ns is not None:
                    marker = _child_plan_globals(ns, _direction(origin, dest))[0]
            markers.append(marker)
        return markers

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

        def _tree(source):
            """The folder a row's outputs stay in. A Source that is a FILE
            writes next to it (or in a subfolder of its folder): its tree is
            its folder, which two file rows of one folder share — `a.tif` and
            `a.tiff` both wrote `a.jxl`, and the scan was skipped (rc6, Fable's
            second sweep D4)."""
            try:
                p = Path(source)
                return str(p.parent) if p.is_file() else source
            except OSError:
                return source

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
                within_source.append(_tree(source))
            elif mode in (6, 7):
                row_marker = (row_markers[i] if row_markers is not None
                              and i < len(row_markers) and row_markers[i]
                              else export_marker)
                marker = self._entry_marker_dir(source, row_marker)
                if marker is None:
                    # The marker is below the Source: outputs land under marker
                    # folders inside this tree.
                    within_source.append(_tree(source))
                    continue
                # The marker is an ANCESTOR of the Source, so the output folder
                # is a sibling of it — shared by every entry under that marker.
                key = os.path.normcase(marker)
                if key in marker_dirs:
                    return True
                marker_dirs[key] = source
            else:
                # 1, 3, 8: a subfolder of the Source, or in place.
                within_source.append(_tree(source))

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
                                    page_options: Optional[Dict] = None,
                                    row_effs: Optional[List[Tuple[Dict, Dict]]] = None) -> List:
        """Find files from DIFFERENT manifest entries that would be written to
        the same output file.

        Each manifest entry runs as a SEPARATE child process, so the children's
        own duplicate-output guard (_abort_on_duplicate_outputs) can never see a
        conflict that spans two entries. Mode 5 is the case that bites: every
        source folder collapses into the same sibling folder, so
        sub1/foto.tif and sub2/foto.tif both target <root>/JXL_16bits/foto.jxl.
        A direct run aborts on this; a manifest run silently converts one and
        skips (or overwrites) the other while reporting 0 errors.

        The CHILD SCRIPT answers, per row (imported lazily, only when
        origin/dest are known): which files the row's run takes and where each
        output goes (_planned_pairs), given the command line that row's run
        gets — build_child_cmd's own argv for the row, parsed by the child
        (_child_plan_args): every flag the run gets reaches the plan, the
        resize, --output-name and the expert flags included (#663, #664).
        Nothing of its rules is copied here (refactor C1). Two blind spots of
        the old flat-Destination scan motivated asking the child at all:

          * Mode 2 is recursive-FLAT: every file under the source lands in the
            Destination, but the old scan only looked at direct children, so
            A/deep/foto.tif + B/foto.tif -> same foto.jxl went unseen.
          * Modes 1/3/4/5/6/7 IGNORE the Destination column and compute their
            output folders from script constants — and an empty Destination
            cell falls back to Source at load time, which bucketed every entry
            separately and detected nothing (the hand-written mode-5 manifest
            this guard was built for).

        Asking the child also honors user-edited folder constants and leaves
        out the files the child would skip (e.g. outside the export marker in
        modes 6/7), keeping the guard free of collisions the child would
        never produce. What stays here is the wrapper's own question: two
        rows on one output, a row's output that is another row's Source
        (cross_sink), the derivatives re-checked only where files collide,
        and the pages of a split TIFF (probed only for page-shaped names).
        The decoder names a TIFF after its page GROUP, read from markers; the
        scan asks it about each JXL, the approximation it has always made.

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
        ExcludeFolders column, ALREADY effective per row. It reaches the
        child's finders through _with_child_marker (its EXCLUDE_FOLDERS
        global), and the legacy flat scan applies it itself, or a
        flat-output row would abort on PHANTOM collisions: the excluded file
        would never be processed at all. When None, no entry has an exclusion
        (the only real caller always passes the list). Only for the
        tiff<->jxl directions — the only ones whose child ever RECEIVES
        --exclude-folders (the cmd builder's emission gate). Filtering
        anywhere else would shrink the walk below what the child processes
        and hide REAL collisions.

        `conversion_type` (optional): the workflow's conversion type, which
        decides the transcoder's command (_TRANSCODER_CONVERSION_FLAGS). The
        transcoder recovers jbrd files with their ORIGINAL name — it ignores
        --rename-from on that path (and on jxl_to_jpeg_lossless entirely);
        its _planned_output reads a JXL's jbrd box only when the two routes
        would name the output differently.

        `page_options` (optional): the run's advanced options, read for
        'multipage_mode'/'thumbnail_mode'/'thumbnail_suffix' (the encoder's
        own settings when absent). A split TIFF writes photo_page1.jxl,
        photo_page2_thumbnail.jxl... next to photo.jxl, and those names can
        land on ANOTHER file's output — photo_page1.tif in a second entry
        (W9 of the 2026-10-08 audit). Only page-shaped names next to their
        base name are probed, so the scan reads a TIFF header only for
        those pairs.

        `row_effs` (optional): one (workflow, advanced_options) per entry,
        aligned with manifest_entries — what each row's run is built from
        (_row_effective_options), so the plan's argv is the run's. Without
        it (a caller of the older shape), each row's workflow is made from
        the arguments above: the conversion type, the row's marker, subfolder,
        folder, exclusion and rename.

        Returns a list of (file_a, file_b, dest_folder) tuples.
        """

        _child = None
        if origin and dest and (origin in ('jpeg', 'jxl') or (origin, dest) == ('tiff', 'jxl')):
            try:
                _child = importlib.import_module(_child_module(origin, dest))
            except ImportError:
                _child = None

        by_dest: Dict[str, Dict[str, Path]] = {}
        collisions = []
        # (output folder, stem) -> [(file, output folder, derivative-skip
        # applies)] — the children's modes 6/7 leave out the JXLs that carry a
        # jxlphoto-derived marker (B2); the scan re-checks only the files
        # that collide, after the walk.
        _members: Dict[Tuple[str, str], List[Tuple[Path, Path, bool]]] = {}
        _derived_capable = _child is not None and (
            _child.__name__ in ('jxl_tiff_decoder', 'jxl_recompressor')
            or (_child.__name__ == 'jxl_jpeg_transcoder' and origin == 'jxl'))
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
                # EXCLUDE_FOLDERS global, where its finders read it.
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
                # The row's own RenameFrom/RenameTo, when the manifest carries
                # the columns; the run-wide pair is the fallback.
                rf, rt = (row_renames[ei] if row_renames is not None
                          else (rename_from, rename_to))
                # The row's run as the child parses it: build_child_cmd's argv
                # for this row (quiet), the builder of the run itself — so
                # the plan gets every flag the run gets (#663, #664). Its
                # marker, folders and exclusion become the child's globals
                # for the walk (_child_plan_globals).
                ns = None
                if _child is not None and mode is not None:
                    if row_effs is not None:
                        _wf_row, _adv_row = row_effs[ei]
                    else:
                        _wf_row = {'origin_format': origin, 'dest_format': dest,
                                   'conversion_type': conversion_type,
                                   'mode_config': {'export_marker': _m,
                                                   'export_subfolder': _s,
                                                   'export_jxl_folder': _j},
                                   'exclude_folders': _excl_eff}
                        _adv_row = {'rename_from': rf, 'rename_to': rt} if rf else {}
                    ns = _child_plan_args(_child, self.build_child_cmd(
                        str(SCRIPT_DIR / f"{_child.__name__}.py"), origin, dest,
                        str(source), dest_path, mode, 1, _wf_row, _adv_row,
                        manifest=True, quiet=True))
                with (_with_child_marker(_child, *_child_plan_globals(
                        ns, _direction(origin, dest)))
                      if ns is not None else nullcontext()):
                    # Per-entry output-tree recording for the cross-entry guard
                    # (only when a sink was handed in).
                    _entry_outs = (cross_sink.setdefault(ei, set())
                                   if cross_sink is not None else None)
                    src_root = Path(source)
                    try:
                        # A file Source is a legitimate manifest row, and the
                        # cross-entry collision guard must see it too.
                        _file_row = src_root.is_file()
                        if not _file_row and not src_root.is_dir():
                            continue
                    except OSError:
                        continue
                    if _child is None or mode is None:
                        # Legacy manifests carry no Mode cell (only a direct
                        # call sees one: the manifest step resolves every row's
                        # mode first), and a direction with no child: the flat
                        # Destination scan by source name is the best the
                        # wrapper can do there (the old behavior for every
                        # entry).
                        if _file_row:
                            outputs = [(src_root, Path(dest_path).parent, None)]
                        else:
                            try:
                                files = sorted(src_root.iterdir())
                            except OSError:
                                continue
                            # Same exclusion as the children's finders (already
                            # gated to the tiff<->jxl directions above): a
                            # legacy TIFF manifest still gets --exclude-folders
                            # from the cmd builder, so the child skips those
                            # files while this flat scan counted them — a
                            # phantom abort.
                            if _excl_names:
                                files = [f for f in files
                                         if not _path_excluded_below_root(
                                             f, src_root, _excl_names)]
                            outputs = [(f, Path(dest_path), False) for f in files
                                       if _is_origin_file(f, origin_exts)]
                        outputs = [(f, o, os.path.normcase(f.stem), el)
                                   for f, o, el in outputs]
                    else:
                        # The child answers WHICH files this row's run takes
                        # and WHERE each output goes (_planned_pairs: its own
                        # finders, exclusion and skips; the mode's folder, the
                        # rename, the jbrd recovery's original name, a
                        # derivative's re-encode), given the command line the
                        # row's run gets (ns, above). Nothing of those rules
                        # is copied here (refactor C1); the derivatives the
                        # child leaves out are checked below, only where files
                        # collide.
                        if ns is None:
                            continue            # the child refuses this command line
                        if not _file_row:
                            # This walk is the slow part on large trees (recursive
                            # glob + per-file stat + resolver per source file) — say so.
                            self.ui.markup(f"[grey70]Collision check: scanning {_rich_safe(src_root)} ...[/grey70]", f"Collision check: scanning {src_root} ...")
                        try:
                            # One walk, one place per file (#665); None-placed
                            # files (outside the export marker in modes 6/7)
                            # are not in the list.
                            pairs = sorted(_child._planned_pairs(ns, read_markers=False),
                                           key=lambda p: p[0])
                        except (OSError, SystemExit):
                            continue
                        # Modes 6/7 with no explicit subfolder: the child leaves
                        # out the derivatives (B2) — never for a single file.
                        # Read inside _with_child_marker, so the row's own
                        # subfolder is the one seen here.
                        _sub_now = (getattr(_child, 'EXPORT_JPEG_SUBFOLDER', '')
                                    if _child.__name__ == 'jxl_jpeg_transcoder'
                                    else getattr(_child, 'EXPORT_JXL_SUBFOLDER', ''))
                        _derived_skip = (None if _file_row else bool(
                            _derived_capable and (mode == 6 or (mode == 7 and not _sub_now))))
                        # A folder row counts the files of the origin format;
                        # the child's finders give files only, so the
                        # extension says it without another stat per file.
                        outputs = [(f, out.parent, os.path.normcase(out.stem), _derived_skip)
                                   for f, out in pairs
                                   if _file_row or f.suffix.lower() in origin_exts]
                    for f, out_folder, stem, el in outputs:
                        if _entry_outs is not None:
                            _entry_outs.add(os.path.normcase(
                                os.path.abspath(str(out_folder))))
                        key = os.path.normcase(str(out_folder))
                        seen = by_dest.setdefault(key, {})
                        # Outputs are named from the stem, so a stem clash is a
                        # clash whatever the target extension is. normcase (not
                        # .lower()): case-sensitive filesystems treat
                        # Foto.tif/foto.tif as distinct files and must NOT
                        # collide. The stem is the output's own (a rename
                        # applied, a jbrd recovery's original name kept).
                        if el is not None:
                            _members.setdefault((key, stem), []).append(
                                (f, out_folder, el))
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
                                 origin: str, dest: str, quiet: bool = False) -> None:
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
            if not quiet:
                self.ui.markup(f"[yellow]{_rich_safe(msg)}[/yellow]", f"WARNING: {msg}")
            pv = 'path'
        cmd.extend(['--provenance', pv])
        # Only the encoder has this flag, and it is inert without adopt.
        if pv == 'adopt' and advanced.get('adopt_scan') is False:
            cmd.append('--no-adopt-scan')

    def _append_overwrite_guard_flag(self, cmd: List, advanced: Dict,
                                     deletes: bool = False) -> None:
        """--no-overwrite-guard when the run turned the guard off. Never with
        a delete (`deletes`: [D], or --delete-source in the expert flags): the
        child refuses that combination (exit 2). The wizard does not offer
        it with [D]; an 'off' a job took from its task's memory before a
        --delete-source was typed in its expert flags stays out (rc6, D15 —
        _gate_guard_with_delete says so before the HHMM)."""
        if advanced.get('overwrite_guard') is False and not deletes:
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
        self.ui.markup(f"[yellow]{_rich_safe(_msg)}[/yellow]", f"WARNING: {_msg}")

    def build_child_cmd(self, script: str, origin: str, dest: str, source: str,
                        dest_path: Optional[str], mode: int, workers: int,
                        workflow: Dict, advanced: Dict, *, manifest: bool,
                        quiet: bool = False) -> List:
        """The child's argv for one run — the direct run (execute_workflow)
        and every manifest entry (_build_manifest_entry_cmd) alike, and the
        plan of that run the wrapper asks the child about (_child_plan_args).

        Every flag comes from REGISTRY: the simple ones through _emit_opt, the
        ones with a rule through the _policy_* methods (and the two older
        _append_* helpers), in a fixed order; the expert flags go LAST. The
        output positional sits right after the input: appending it after the
        flags breaks argparse on Python < 3.12.7 (gh-59317). `manifest`
        selects the manifest-only rules: --summary-json, the at-most-once
        exclude warning, the derivative row's suppressed delete and the
        transcoder's rename on its lossy decode (the direct wizard refuses
        those combinations before it gets here). `quiet`: the same argv
        without the notes some rules print (the plan's; the run prints them)."""
        ctx = _CmdCtx(origin, dest, source, dest_path, mode, workers, workflow,
                      advanced, manifest, self.config.config.export_marker, quiet)
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
        self._append_provenance_flags(cmd, ctx.advanced, origin, dest, quiet=quiet)
        self._append_overwrite_guard_flag(
            cmd, ctx.advanced,
            deletes=('--delete-source' in cmd
                     or _flags_request_delete(workflow.get('expert_flags'), origin, dest)))
        self._policy_format(cmd, ctx)
        _append_expert_flags(cmd, workflow.get('expert_flags'), origin, dest)
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
        elif not ctx.quiet:
            self._warn_exclude_folders_unsupported(ctx.origin, ctx.dest, excl,
                                                   at_most_once=ctx.manifest)

    def _policy_conversion(self, cmd: List, ctx: "_CmdCtx") -> None:
        """The transcoder's conversion type (_TRANSCODER_CONVERSION_FLAGS)."""
        if ctx.direction not in _TRANSCODER_DIRECTIONS:
            return
        cmd.extend(_TRANSCODER_CONVERSION_FLAGS.get(ctx.conv_type, ()))

    def _policy_rename(self, cmd: List, ctx: "_CmdCtx") -> None:
        """--rename-from/--rename-to, always as a pair, where the child takes
        it (_rename_reaches_child)."""
        rf = ctx.advanced.get('rename_from')
        if not rf:
            return
        if _rename_reaches_child(ctx.direction, ctx.decode_lossy, ctx.manifest):
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
        # A manifest row carries the in-place answer its confirmation was
        # asked for (`_in_place_planned`, made before the first row): the
        # disk read again here could see the JXLs an EARLIER row of the same
        # run had just written into this row's Destination, and the flag went
        # out with no confirmation for it (rc6's second review, S1). Without
        # one (a direct run, the plan's own argv) the disk answers.
        _planned = ctx.workflow.get('_in_place_planned') if ctx.manifest else None
        if ctx.direction == "jxl2jxl" and (
                _planned if _planned is not None
                else _recompress_entry_in_place(
                    ctx.source, ctx.dest_path, ctx.mode,
                    _mode_config_with_marker(ctx.mode_config, ctx.config_marker),
                    ctx.workflow.get('expert_flags'))):
            cmd.append('--delete-confirm-off')
        delete = bool(adv.get('delete_source'))
        if delete and ctx.manifest and ctx.direction in _TRANSCODER_DIRECTIONS:
            if ctx.workflow.get('icc_profile') and not ctx.derivative and not ctx.quiet:
                _note = ("Colour conversion (--icc-profile) writes a DERIVATIVE: the "
                         "originals are NOT deleted on this run.")
                self.ui.markup(f"[yellow]{_rich_safe(_note)}[/yellow]", _note)
            delete = not ctx.derivative and not ctx.workflow.get('icc_profile')
            _never = _lossy_decode_never_deletes(ctx.workflow) if delete else None
            if _never:
                # T-B: the row converts, its sources stay (the child would exit 2).
                if not ctx.quiet:
                    _note = f"No delete for this row: {_never}."
                    self.ui.markup(f"[yellow]{_rich_safe(_note)}[/yellow]", _note)
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
        cmd.extend(_transcoder_format_flags(ctx.dest))

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
        self._last_child_rc = None

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

        # C4: a Ctrl+C while the child runs is the child's to handle (it
        # finishes its files in progress and prints its summary); the wizard
        # only counts the presses here and acts on them in the loop below —
        # never print from the handler (Rich is not re-entrant).
        import signal
        presses = []
        try:
            previous = signal.signal(signal.SIGINT, lambda s, f: presses.append(time.time()))
            installed = True
        except (ValueError, OSError):
            previous, installed = None, False
        announced = 0

        deadline = time.time() + idle_timeout
        try:
            try:
                while True:
                    if len(presses) >= 3 or (len(presses) >= 2
                                             and time.time() - presses[1] >= _CHILD_STOP_GRACE):
                        _kill_process_tree(process)
                        raise KeyboardInterrupt
                    if len(presses) > announced:
                        announced = len(presses)
                        _note = ("Ctrl+C: the script finishes the files in progress and prints "
                                 "its summary. Ctrl+C again stops it now."
                                 if announced == 1 else
                                 f"Ctrl+C again: stopping now (up to {_CHILD_STOP_GRACE} s for "
                                 f"the script to clean up).")
                        self.ui.markup(f"[yellow]{_rich_safe(_note)}[/yellow]", _note)
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        _kill_process_tree(process)
                        self._print_error(
                            f"No output for {idle_timeout // 60} min: process killed "
                            f"(hidden prompt, hang, or a single file slower than that "
                            f"— the child prints only on completed files)")
                        return -1
                    try:
                        # A short wait (C4): two presses that come while this
                        # thread waits reach the handler as ONE (Python runs it
                        # once per wake-up), and a quick second press must
                        # still count as the second.
                        line = q.get(timeout=min(0.2, max(remaining, 0.1)))
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
                if presses:
                    raise KeyboardInterrupt
                return -1
            self._last_child_rc = process.returncode
            if presses:
                # The child was left to end by itself (C4); the wizard ends as it
                # always did after a Ctrl+C.
                raise KeyboardInterrupt
            return process.returncode
        finally:
            if installed:
                try:
                    signal.signal(signal.SIGINT, previous)
                except (ValueError, OSError):
                    pass

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
        _flag_delete = _flags_request_delete(workflow.get('expert_flags'), origin, dest)
        _adv_delete = bool(workflow.get('advanced_options', {}).get('delete_source'))
        # JXL -> JXL in modes 0/8 REPLACES the source files with the
        # recompressed versions — as irreversible as a delete, so the same
        # proof-of-presence gate applies. (The wrapper only ever passes an
        # output positional to the recompressor in mode 2, so mode 0 is
        # always in place here.)
        # The same test as the command's --delete-confirm-off (_policy_delete):
        # a mode-2 run whose output folder, inside the input folder, already
        # holds JXLs replaces them in place too, and was sent the flag without
        # this confirmation (the review of rc6, M1 — D13's predicate).
        _in_place_recompress = _jxl_run_replaces_in_place(
            origin, dest, workflow.get('mode'), workflow.get('input_dir'),
            (workflow.get('mode_config') or {}).get('output_dir'),
            _mode_config_with_marker(workflow.get('mode_config'), _menu_marker(self)),
            workflow.get('expert_flags'))
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
            console.print("\n[bold cyan]Executing:[/bold cyan]")
            console.print(f"[grey70]{_rich_safe(' '.join(_display_cmd(cmd)))}[/grey70]\n")
        else:
            print(f"\nExecuting: {' '.join(_display_cmd(cmd))}\n")

        returncode = self._stream_child(cmd)

        if returncode == 0:
            self._print_success("Dry run finished — nothing was written.\n"
                                if workflow.get('dry_run')
                                else "Conversion completed successfully!\n")
            return True
        elif returncode == 3:
            self._print_error("Conversion cancelled (confirmation declined)")
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
                self._print_error("Aborted by a safety check (see log above — e.g. duplicate output destinations or output/input collisions)")
            return False
        else:
            self._print_error(f"Conversion failed (code {returncode})")
            return False

    @staticmethod
    def _describe_session(session: Dict) -> str:
        """One-line summary of a stored workflow, for the preset list."""
        # Coerced like _run_saved_session does: a mode stored as the NUMBER 99
        # (or as "99.0" by a hand edit) still has to read as a manifest here,
        # or the row advertises "mode 99" for a run that replays a CSV.
        raw_mode = session.get('last_output_mode')
        mode = _session_int(session, 'last_output_mode', -1)
        bits = [_task_label(session.get('last_origin_format'), session.get('last_dest_format'),
                            session.get('last_conversion_type'))]
        if mode == 99:
            manifest = session.get('last_manifest_path')
            bits.append(f"manifest: {Path(manifest).name}" if manifest else "manifest: (missing)")
        else:
            bits.append(f"mode {mode if mode >= 0 else (raw_mode or '?')}")
        _folder_bit = (str(session['last_input_dir'])
                       if mode != 99 and session.get('last_input_dir') else None)
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
        if _folder_bit:
            bits.append(_folder_bit)
        return " · ".join(bits)

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

            ui = self.ui
            if ui.is_rich:
                if names:
                    table = Table(show_header=True, header_style="bold cyan", box=BOX_SIMPLE)
                    table.add_column("#", justify="right", style="dim")
                    table.add_column("Name", style="bold")
                    table.add_column("Job")
                    table.add_column("Saved", style="dim")
                    for i, name in enumerate(names, 1):
                        table.add_row(str(i), _rich_safe(name), _rich_safe(self._describe_session(_preset_session(presets[name]))),
                                      presets[name].get('saved_at', ''))
                    console.print(Panel(table, title="[bold]Presets[/bold]", border_style="green"))
                else:
                    console.print("\n[grey70]No presets saved yet.[/grey70]")
                ui.actions([("Run", [(f"1-{len(names)}" if names else "number", "run a preset")]),
                            ("Save", [("s", "save the last job as a preset")]),
                            ("Leave", [("d", "delete a preset"), ("b", "back")])])
            else:
                ui.markup(None, "\n--- Presets ---")
                if names:
                    for i, name in enumerate(names, 1):
                        ui.markup(None, f"  {i}. {name}")
                        ui.markup(None, f"     {self._describe_session(_preset_session(presets[name]))}")
                else:
                    ui.markup(None, "  (none saved yet)")
                ui.markup(None, "[number] run | [s] save the last job as a preset | [d] delete | [b] back")
            choice = ui.ask_line("presets.menu", "Presets", "Presets: ")

            upper = choice.upper()
            if not choice or upper == "B":
                return

            if upper == "S":
                snapshot = self.config.session_snapshot()
                if not snapshot.get('last_input_dir'):
                    self._print_error("No workflow has been run yet — run one first, then save it here.")
                    continue
                ui.markup(f"[grey70]{_rich_safe(self._describe_session(snapshot))}[/grey70]",
                          f"  {self._describe_session(snapshot)}")
                raw_name = ui.ask_line("presets.name", "Preset name", "Preset name: ")
                name = self._clean_preset_name(raw_name)
                if not name:
                    self._print_error("Invalid name (empty, too long, or contains control characters).")
                    continue
                if name in presets:
                    overwrite = ui.ask_yes(
                        "presets.overwrite", f"Preset '{_rich_safe(name)}' already exists. Overwrite?",
                        f"Preset '{name}' exists. Overwrite? [y/N]: ", False)
                    if not overwrite:
                        continue
                self.config.save_preset(name, snapshot)
                self._print_success(f"Preset '{name}' saved.")
                continue

            if upper == "D":
                if not names:
                    continue
                target = ui.ask_line("presets.delete", "Delete which number?",
                                     "Delete which number?: ")
                if not target.isdigit() or not 1 <= int(target) <= len(names):
                    self._print_error("No preset with that number.")
                    continue
                name = names[int(target) - 1]
                sure = ui.ask_yes("presets.delete_sure", f"Delete preset '{_rich_safe(name)}'?",
                                  f"Delete preset '{name}'? [y/N]: ", False)
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
        last_workers = _session_int(session, 'last_workers',
                                    self.config.config.default_workers)
        last_staging = session.get('last_staging') or ""
        # #626: absent = None, so the builder reads the child's own setting
        # (_workflow_or_child) instead of a literal 7 / 95 / 0.1.
        last_effort = _session_int(session, 'last_effort', None)
        last_quality = _session_int(session, 'last_quality', None)
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
        # What the run will really use, for the panel only (#626): the saved
        # value, else the child's own setting.
        _mod = _child_module(last_origin, last_dest)
        _shown_effort = (last_effort if last_effort is not None
                         else (_child_default(_mod, 'effort')
                               if 'effort' in _CHILD_OPTION_DEFAULTS.get(_mod, {}) else None))
        _shown_quality = (last_quality if last_quality is not None
                          else _child_default('jxl_jpeg_transcoder', 'quality'))
        _shown_distance = (last_distance if last_distance is not None
                           else (_child_default(_mod, 'distance')
                                 if 'distance' in _CHILD_OPTION_DEFAULTS.get(_mod, {}) else None))
        # Mode 2 writes into the folder the job chose: shown (rc6, D11).
        _saved_out = ((session.get('last_mode_config') or {}).get('output_dir')
                      if last_mode == 2 and not is_manifest_repeat else None)
        # A delete typed in the expert flags is a delete on this panel too
        # (rc6, D15): it showed the guard's 'off', which such a run never gets.
        _flag_delete_shown = _flags_request_delete(session.get('last_expert_flags'),
                                                   last_origin, last_dest)

        if RICH_AVAILABLE and console:
            settings = [
                ["Manifest", f"{Path(manifest_path).name} ({len(manifest_entries)} entries)"]
                if is_manifest_repeat else ["Input folder", last_dir],
                ["Source", last_origin.upper()],
                ["Destination", last_dest.upper()],
                ["Mode", str(last_mode)],
                ["Workers", str(last_workers)],
            ]
            if _saved_out:
                settings.insert(4, ["Output folder", _saved_out])
            if _shown_effort is not None:
                settings.append(["Effort", str(_shown_effort)])
            # Show the knob that actually steers this run (see above).
            if _distance_driven and _shown_distance is not None:
                settings.append(["Distance", f"{_shown_distance}"])
            elif not _distance_driven and _quality_driven and _shown_quality is not None:
                settings.append(["Quality", str(_shown_quality)])
            settings.append(["Staging", last_staging or "system default"])
            # Surface silently-reapplied dangerous settings (they are NOT
            # re-asked on this path, so the user must see them here).
            _last_adv = session.get('last_advanced_options') or {}
            if _last_adv.get('delete_source'):
                settings.append(["Delete source", "ON"])
            elif _flag_delete_shown:                               # rc6, D15
                settings.append(["Delete source", "ON (expert flags)"])
            elif session.get('last_overwrite_guard') is False:     # #707
                settings.append(["Overwrite guard", "off"])
            if session.get('last_expert_flags'):
                settings.append(["Expert flags", session.get('last_expert_flags')])
            
            t = Table(box=BOX_SIMPLE, show_header=False, pad_edge=False)
            t.add_column("", style="cyan")
            t.add_column("")
            for row in settings:
                t.add_row(*(_rich_safe(c) for c in row))
            console.print(Panel(t, title="[bold]Last job[/bold]", border_style="green"))
        else:
            print("\n=== Last job ===")
            if is_manifest_repeat:
                print(f"  Manifest:     {Path(manifest_path).name} ({len(manifest_entries)} entries)")
            else:
                print(f"  Input folder: {last_dir}")
            print(f"  Source:       {last_origin.upper()}")
            print(f"  Destination:  {last_dest.upper()}")
            print(f"  Mode:         {last_mode}")
            if _saved_out:
                print(f"  Output folder: {_saved_out}")
            print(f"  Workers:      {last_workers}")
            if _shown_effort is not None:
                print(f"  Effort:       {_shown_effort}")
            # Same rule as the rich panel: the knob that actually steers this
            # run (see _distance_driven/_quality_driven above, mirror of
            # _describe_session).
            if _distance_driven and _shown_distance is not None:
                print(f"  Distance:     {_shown_distance}")
            elif not _distance_driven and _quality_driven and _shown_quality is not None:
                print(f"  Quality:      {_shown_quality}")
            print(f"  Staging:      {last_staging or '(none)'}")
            _last_adv = session.get('last_advanced_options') or {}
            if _last_adv.get('delete_source'):
                print("  Delete source: ON")
            elif _flag_delete_shown:                               # rc6, D15
                print("  Delete source: ON (expert flags)")
            elif session.get('last_overwrite_guard') is False:     # #707
                print("  Overwrite guard: off")
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
            # Enter keeps the stored folder (just below).
            new_folder = self.ui.ask_line(
                "session.folder", "\n[bold cyan]Input folder[/bold cyan]",
                f"\nInput folder [{last_dir}]: ",
                default=(last_dir if self.ui.is_rich else None))

            if not new_folder:
                new_folder = last_dir

            new_folder = _strip_surrounding_quotes(new_folder)
            input_path = Path(new_folder)
            if not input_path.exists():
                self.ui.markup(f"[red]Folder not found: {_rich_safe(new_folder)}[/red]", f"Folder not found: {new_folder}")
                return False

        origin = last_origin

        if unattended:
            ow_choice = "1" if answers.get('overwrite') else "2"
        else:
            self.ui.markup("Existing outputs: [1] overwrite all   [2] sync (only the newer sources)",
                           _SILENT)
            # Anything but 1 in the plain prompt is sync, as before.
            ow_choice = self.ui.ask_line("session.overwrite", "Existing outputs",
                                         "Existing outputs (1=overwrite all, 2=sync) [2]: ",
                                         default="2", choices=["1", "2"])

        if ow_choice == "1":
            overwrite, sync = True, False
        else:
            overwrite, sync = False, True

        # Ask dry-run explicitly — the original run may have been a
        # simulation, and hardcoding False would turn a repeat into a REAL
        # conversion without any notice.
        if unattended:
            dry_choice = bool(answers.get('dry_run'))
        else:
            dry_choice = self.ui.ask_yes("session.dry_run", "Dry run first (nothing written)?",
                                         "Dry run first (nothing written)? [y/N]: ", False)

        if unattended:
            # A destructive preset cannot run unattended (_GATE_RULES, path
            # "preset": delete in any mode or in the expert flags, or an
            # in-place recompression).
            if _job_gates(self, {}, "preset", session=session, dry_run=dry_choice,
                          origin=origin, dest=last_dest, mode=last_mode):
                return False
            proceed = True
        else:
            proceed = self.ui.ask_yes("session.proceed", "[bold]Run this job?[/bold]",
                                      "Run this job? [Y/n]: ", True)

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
                'distance': last_distance,
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
            # old destination would silently swallow the new outputs. The
            # SAME folder typed another way (a '\' at the end, other case) is
            # the same folder: compared as text, the job's chosen destination
            # was swapped for <parent>\output, unseen (rc6, Fable's second
            # sweep D11). The folder it takes is said either way.
            if saved_out and _same_folder(new_folder, last_dir):
                workflow['mode_config'] = {'output_dir': saved_out}
            else:
                workflow['mode_config'] = {'output_dir': str(input_path.parent / "output")}
            _out_note = f"Output folder: {workflow['mode_config']['output_dir']}"
            self.ui.markup(f"[cyan]{_rich_safe(_out_note)}[/cyan]", _out_note)
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
            if (workflow['advanced_options'].get('delete_source')
                    or _flags_request_delete(session.get('last_expert_flags'),
                                             origin, last_dest)):     # rc6, D15
                # No job that deletes ran with the guard off: every script
                # refuses the pair (exit 2, after the HHMM). A session saved
                # before #707 can carry an older job's "off": not this job's.
                # On the screen: the wrapper's logger shows WARNING and up only
                # (the second review of rc5, N2: an INFO line never showed).
                self.ui.warn("The overwrite guard stays on for this replay: an earlier "
                             "job's 'off' does not apply to a run that deletes sources.")
            else:
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
        self.ui.markup(f"[green]✓[/green] {_rich_safe(message)}", f"✓ {message}")

    def _print_error(self, message: str) -> None:
        self.ui.markup(f"[red]✗[/red] {_rich_safe(message)}", f"✗ {message}")


# Where Explorer looks up a .jxl thumbnail handler (the IThumbnailProvider
# shell-extension GUID), per extension or under SystemFileAssociations.
_THUMBNAIL_HANDLER_KEYS = (
    r".jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}",
    r"SystemFileAssociations\.jxl\ShellEx\{e357fccd-a995-4576-b01f-234630154e96}",
)


# jxl-thumb's thumbnail provider (the recompressor's _JXL_THUMB_CLSID).
_JXL_THUMB_CLSID = "{EF830030-FBD6-4B62-BFD4-0DBD590D6135}"


def _dll_file_version(path) -> Optional[str]:
    """"major.minor.patch" of a Windows DLL's fixed file version (its
    VERSIONINFO resource), or None: not Windows, no resource, any error.
    Reads the file's header only; never loads or runs it."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        ver = ctypes.WinDLL("version")
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        ptr, n = ctypes.c_void_p(), wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(n)) \
                or n.value < 52:
            return None
        fixed = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        if fixed[0] != 0xFEEF04BD:           # VS_FIXEDFILEINFO's signature
            return None
        ms, ls = fixed[2], fixed[3]          # dwFileVersionMS / LS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}"
    except Exception:
        return None


def _jxl_thumb_dll_version() -> Optional[str]:
    """The installed jxl-thumb's version ("0.2.1"), from the DLL its CLSID is
    registered to, or None (not installed, not Windows, unreadable).
    JXLPHOTO_JXL_THUMB=0 (the test suite, the battery) reads as not
    installed."""
    if os.name != "nt" or os.environ.get("JXLPHOTO_JXL_THUMB") == "0":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            rf"CLSID\{_JXL_THUMB_CLSID}\InprocServer32") as key:
            dll = str(winreg.QueryValue(key, None) or "").strip()
    except Exception:
        return None
    return _dll_file_version(dll) if dll and os.path.isfile(dll) else None


def _thumb_update_notice(config_manager) -> Optional[str]:
    """The menu line when the installed jxl-thumb is older than its latest
    release (kept by the daily check, thumb_latest_seen), else None. No
    network here; off like the release check."""
    cfg = config_manager.config
    if not cfg.check_for_updates or os.environ.get("JXLPHOTO_NO_UPDATE_CHECK"):
        return None
    mine = _jxl_thumb_dll_version()
    km, kt = _version_key(mine), _version_key(cfg.thumb_latest_seen)
    if not km or not kt or kt[:3] <= km[:3]:
        return None
    return (f"jxl-thumb {mine} is installed; {cfg.thumb_latest_seen.lstrip('v')} is out "
            f"— [T] in the menu opens its page.")


def _thumb_latest_if_installed(timeout: float = None) -> Optional[str]:
    """jxl-thumb's latest release tag, asked only when jxl-thumb is installed."""
    if not _jxl_thumb_dll_version():
        return None
    return _latest_release_tag("jxl-thumb", **({} if timeout is None else {"timeout": timeout}))


_UPDATE_OWNER = "rsilvabr"
_UPDATE_TIMEOUT = 3          # seconds: a slow or absent network stays silent


def _update_repo() -> str:
    """The repository whose releases this copy follows: the Windows program
    (a frozen build) follows jxl-photo-exe, the scripts follow jxl-photo."""
    return "jxl-photo-exe" if getattr(sys, "frozen", False) else "jxl-photo"


def _version_key(text: Optional[str]):
    """(major, minor, patch, final) of "v2.22.0", "2.30.1-rc1" (a test
    release of 2.30.1), "2.22.0_beta1" (the test releases' name until
    v2.30.0), "2.31.1-dev1" (a development version: commits run from the
    repo or a local copy before 2.31.1, tagged but never published) or
    "v2.22.0-exe1" (the exe's package revision is ignored: exe2 of the same
    release is not news here), or None. A test or development version sorts
    before its release: a copy running 2.30.1-rc2 is told when 2.30.1 is out
    (GitHub's "latest" never names a test release itself)."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(_beta\d+|-rc\d+|-dev\d+)?(?:-exe\d+)?$",
                 (text or "").strip())
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
        thumb = _thumb_latest_if_installed()
        # No answer (no network, a timeout, a rate limit): the day is NOT
        # marked, so the next run asks again (owner's request, 2026-11-08).
        if tag:
            try:
                config_manager.save_update_answer(today, tag, thumb)
            except Exception:
                pass
    return _kept_update_notice(config_manager)


def _kept_update_notice(config_manager, checked_before: bool = False
                        ) -> Optional[Tuple[str, str]]:
    """The notice from the answer kept in the config (no network), or None.
    checked_before: the answer came from an earlier run (it was asked in the
    background while that run was busy), so the line says when, and that D
    asks again."""
    cfg = config_manager.config
    if not cfg.check_for_updates or os.environ.get("JXLPHOTO_NO_UPDATE_CHECK"):
        return None
    latest = cfg.update_latest_seen
    mine, theirs = _version_key(TOOLKIT_VERSION), _version_key(latest)
    if not mine or not theirs or theirs[:3] < mine[:3] or theirs <= mine:
        return None
    url = f"https://github.com/{_UPDATE_OWNER}/{_update_repo()}/releases/latest"
    line = (f"A new release is out: {latest} (this copy is {TOOLKIT_VERSION}) — "
            f"[U] in the menu opens its page.")
    if checked_before and cfg.update_checked_on:
        line += (f" (Checked by an earlier run on {cfg.update_checked_on}; "
                 f"D in the menu checks again.)")
    return (line, url)


def _update_check_start(config_manager):
    """The daily release lookup without making the menu wait: GitHub is asked
    in a background thread, which KEEPS the answer itself (save_update_answer)
    as soon as it is in — the menu need not be drawn again, so the next run
    shows it even when this one went straight into a job and was closed.
    Returns poll(wait=0.0) -> the notice or None: until the answer is in, the
    one an earlier run kept (with its date); after it, the fresh one.
    Off: no thread, no notice. Already asked today: no thread, the kept
    answer (with its date)."""
    cfg = config_manager.config
    if not cfg.check_for_updates or os.environ.get("JXLPHOTO_NO_UPDATE_CHECK"):
        return lambda wait=0.0: None
    from datetime import date
    today = date.today().isoformat()
    if cfg.update_checked_on == today:
        notice = _kept_update_notice(config_manager, checked_before=True)
        return lambda wait=0.0: notice
    import threading
    fresh: Dict[str, bool] = {"answered": False}

    def _ask():
        tag = _latest_release_tag(_update_repo())
        thumb = _thumb_latest_if_installed()
        if tag:                          # no answer: the next run asks again
            try:
                config_manager.save_update_answer(today, tag, thumb)
            except Exception:
                pass
            fresh["answered"] = True

    worker = threading.Thread(target=_ask, name="update-check", daemon=True)
    worker.start()

    def poll(wait: float = 0.0):
        if worker.is_alive():
            worker.join(wait)
        answered = not worker.is_alive() and fresh["answered"]
        return _kept_update_notice(config_manager, checked_before=not answered)
    return poll


def _update_check_now(config_manager) -> Tuple[str, Optional[Tuple[str, str]]]:
    """D in the menu: ask GitHub now (whatever was asked today) and keep the
    answer. Returns (the line to print, the notice or None)."""
    cfg = config_manager.config
    if not cfg.check_for_updates or os.environ.get("JXLPHOTO_NO_UPDATE_CHECK"):
        return ("Release check: off (check_for_updates in the settings file, "
                "or JXLPHOTO_NO_UPDATE_CHECK).", None)
    from datetime import date
    tag = _latest_release_tag(_update_repo())
    thumb = _thumb_latest_if_installed()
    if not tag:
        return (f"Release check: GitHub did not answer (this copy is "
                f"{TOOLKIT_VERSION}).", None)
    try:
        config_manager.save_update_answer(date.today().isoformat(), tag, thumb)
    except Exception:
        pass
    notice = _kept_update_notice(config_manager)
    if notice:
        return (notice[0], notice)
    return (f"Release check: up to date ({tag} is the latest release; this copy "
            f"is {TOOLKIT_VERSION}).", None)


def _jxl_thumbnail_tip() -> Optional[str]:
    """The menu's one-line tip when jxl-thumb is not Explorer's thumbnail
    handler for .jxl: no handler at all, or Microsoft's JPEG XL extension
    (slow, and ProPhoto/Adobe RGB look washed out). None with jxl-thumb.

    One registry read, nothing written; any error means no tip, never a
    blocked menu. JXLPHOTO_THUMBNAIL_TIP replaces the text (a packaged build
    names its own install command); an empty value turns the tip off.
    """
    if os.name != "nt":
        return None
    handler = ""
    try:
        import winreg
        for sub in _THUMBNAIL_HANDLER_KEYS:
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, sub) as key:
                    # Stock Windows 11 has the SystemFileAssociations key with
                    # an empty value: that registers nothing.
                    handler = str(winreg.QueryValue(key, None) or "").strip()
            except OSError:
                continue
            if handler:
                break
    except Exception:
        return None
    if handler.upper() == _JXL_THUMB_CLSID:
        return None
    override = os.environ.get("JXLPHOTO_THUMBNAIL_TIP")
    if override is not None:
        return override.strip() or None
    if handler:
        return ("Tip: Explorer's JXL thumbnails come from Microsoft's extension "
                "here: slow, and ProPhoto/Adobe RGB photos look washed out. "
                f"jxl-thumb is about 40x faster, with the right colours: "
                f"{JXL_THUMB_URL} ([T] in the menu opens it)")
    return (f"Tip: Windows Explorer shows no thumbnails for JXL files on this PC. "
            f"jxl-thumb adds them: {JXL_THUMB_URL} ([T] in the menu opens it)")


# === SETUP (jxl_photo.py setup) ==============================================
#
# `python jxl_photo.py setup`: the external tools the scripts find on PATH
# (libjxl, ExifTool, ImageMagick) and the Python packages: what a program
# started NOW runs, what is wrong with it, and the fixes. PATH values are
# written only through _write_path (compare-and-swap, a backup, the broadcast),
# never setx; the system PATH only through the elevated --apply-system-path
# plan. Tests and tools/ui_tour.py set JXLPHOTO_SETUP_PATH_STORE (a JSON copy
# of the values): nothing of theirs ever reaches the registry.

_REG_SZ = 1
_REG_EXPAND_SZ = 2

# The versions jxl-photo-exe's build.py ships (TOOLS, REQUIREMENTS) and
# docs/install.md names: change the three together.
_SETUP_TESTED_TOOLS = {
    "libjxl": {
        "version": "0.13.0",
        "url": "https://github.com/libjxl/libjxl/releases/download/v0.13.0/"
               "jxl-x64-windows-static.7z",
        "sha256": "d97b753ffcd0f992db2ab3ef7e896a6ba425cafe8332e5e6df6376f70e2b9ada",
        "archive": "jxl-x64-windows-static.7z",
    },
    "exiftool": {
        "version": "13.59",
        "url": "https://sourceforge.net/projects/exiftool/files/exiftool-13.59_64.zip/download",
        "sha256": "44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec",
        "archive": "exiftool-13.59_64.zip",
    },
    "magick": {
        "version": "7.1.2-27",
        "url": "https://github.com/ImageMagick/ImageMagick/releases/download/7.1.2-27/"
               "ImageMagick-7.1.2-27-portable-Q16-HDRI-x64.7z",
        "sha256": "c7d6f13b3021db5d2e23876d85f06d6039a9a345236f0d32cdb73886ddfe70ed",
        "archive": "ImageMagick-7.1.2-27-portable-Q16-HDRI-x64.7z",
    },
}
_SETUP_TESTED_PACKAGES = {"numpy": "2.5.1", "tifffile": "2026.6.1",
                          "imagecodecs": "2026.6.26", "pillow": "12.3.0", "rich": "15.0.0"}

# Below these a tool is refused (libjxl: the encoder's _warn_if_libjxl_too_old
# number) or only warned about (ExifTool: one process per command below 12.15).
_SETUP_MIN_LIBJXL = (0, 11, 2)
_SETUP_MIN_EXIFTOOL = (12, 15)
_SETUP_MIN_MAGICK_MAJOR = 7
# The packages without which the encoder and the decoder do not run at all.
_SETUP_PACKAGES_REQUIRED = ("numpy", "tifffile")
_SETUP_MAX_DOWNLOAD = 400 * 1024 * 1024
_SETUP_ELEVATED_WAIT = 180
_SETUP_RESTART_NOTE = ("Programs already open keep their old PATH: open a new window. "
                       "A scheduled task may need you to sign out and in again.")
_SETUP_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_SETUP_REG_WHERE = {
    "user": ("HKEY_CURRENT_USER", r"Environment"),
    "system": ("HKEY_LOCAL_MACHINE",
               r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
}
# Printed in a package's line of the probe below: (name, module, distribution).
_SETUP_PACKAGE_MODULES = (("numpy", "numpy", "numpy"), ("tifffile", "tifffile", "tifffile"),
                          ("imagecodecs", "imagecodecs", "imagecodecs"),
                          ("pillow", "PIL", "pillow"), ("rich", "rich", "rich"))
# Run by the interpreter in a process of its own: a package installed a moment
# ago by this setup must show, and the setup itself never imports them.
_SETUP_PACKAGE_PROBE = (
    "import importlib, json\n"
    "try:\n"
    "    from importlib import metadata\n"
    "except ImportError:\n"
    "    metadata = None\n"
    "out = {}\n"
    "for name, mod, dist in %r:\n"
    "    try:\n"
    "        m = importlib.import_module(mod)\n"
    "    except Exception:\n"
    "        out[name] = None\n"
    "        continue\n"
    "    try:\n"
    "        v = metadata.version(dist)\n"
    "    except Exception:\n"
    "        v = getattr(m, '__version__', None)\n"
    "    out[name] = str(v) if v else '?'\n"
    "print(json.dumps(out))\n" % (_SETUP_PACKAGE_MODULES,))


class _PathStore:
    """The PATH values in the registry (Windows): HKCU\\Environment for the
    user, the Session Manager's Environment for the system. Only
    _apply_system_path_plan writes the system one (as administrator)."""
    elevation_needed = True
    writable = True
    where = {"user": r"HKEY_CURRENT_USER\Environment",
             "system": r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"}

    @staticmethod
    def _open(scope, access):
        import winreg
        root, sub = _SETUP_REG_WHERE[scope]
        return winreg.OpenKey(getattr(winreg, root), sub, 0, access)

    def read(self, scope):
        import winreg
        try:
            with self._open(scope, winreg.KEY_READ) as key:
                value, regtype = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return None, None
        if not isinstance(value, str):
            # A REG_MULTI_SZ or binary Path: nothing this setup should edit.
            raise OSError(f"the {scope} Path value is not text (registry type {regtype})")
        return value, int(regtype)

    def write(self, scope, value, regtype):
        import winreg
        with self._open(scope, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "Path", 0, int(regtype), value)

    def broadcast(self) -> bool:
        return _broadcast_environment()


class _JsonPathStore:
    """The same interface on a JSON file {"user": {"value", "type"},
    "system": {...}} (JXLPHOTO_SETUP_PATH_STORE): the suite, ui_tour and a
    by-hand try never reach the registry. A missing file = both empty."""
    elevation_needed = False
    writable = True

    def __init__(self, file):
        self.file = Path(file)
        self.where = {"user": f"user PATH of {self.file}", "system": f"system PATH of {self.file}"}

    def _load(self) -> Dict:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        return data if isinstance(data, dict) else {}

    def read(self, scope):
        entry = self._load().get(scope) or {}
        value, regtype = entry.get("value"), entry.get("type")
        return (value if isinstance(value, str) else None,
                regtype if isinstance(regtype, int) and not isinstance(regtype, bool) else None)

    def write(self, scope, value, regtype):
        data = self._load()
        data[scope] = {"value": value, "type": int(regtype)}
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_name(f"{self.file.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.file)

    def broadcast(self) -> bool:
        return True


class _EnvPathStore:
    """Linux / macOS: this shell's PATH, read only. Every PATH action prints
    the line to add to the shell profile instead (A7 of the plan)."""
    elevation_needed = False
    writable = False
    where = {"user": "this shell's PATH", "system": "(none)"}

    def read(self, scope):
        if scope != "user":
            return None, None
        value = os.environ.get("PATH")
        if value is None:
            return None, None
        return (value.replace(os.pathsep, ";") if os.pathsep != ";" else value), None

    def write(self, scope, value, regtype):
        raise NotImplementedError("the PATH is set in the shell profile here")

    def broadcast(self) -> bool:
        return False


def _path_store():
    """Where the setup reads and writes PATH values."""
    file = os.environ.get("JXLPHOTO_SETUP_PATH_STORE")
    if file:
        return _JsonPathStore(file)
    if os.name == "nt":
        return _PathStore()
    return _EnvPathStore()


def _broadcast_environment() -> bool:
    """Tell running programs (Explorer first) that the environment changed:
    WM_SETTINGCHANGE "Environment". A failure is a note, never an error."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        send = user32.SendMessageTimeoutW
        send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                         wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = ctypes.c_ssize_t
        result = ctypes.c_size_t(0)
        return bool(send(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result)))
    except Exception:
        return False


# --- PATH strings ------------------------------------------------------------

def _path_entries(value) -> List[str]:
    """The entries of a PATH value, each as written (only the whitespace
    around it stripped); empty ones dropped."""
    return [e.strip() for e in (value or "").split(";") if e.strip()]


def _expand_env(text: str) -> str:
    if os.name == "nt":
        try:
            import winreg
            return winreg.ExpandEnvironmentStrings(text)
        except Exception:
            return text
    return os.path.expandvars(text)


def _expand_entry(entry: str) -> str:
    """The folder a PATH entry names: quotes off, %VAR%s expanded."""
    return _expand_env(entry.strip().strip('"'))


def _path_key(entry: str) -> str:
    """What two PATH entries are compared by: the expanded, normalised,
    case-folded folder without a trailing separator (a drive root keeps it)."""
    text = _expand_entry(entry)
    if not text:
        return ""
    text = os.path.normcase(os.path.normpath(text))
    if len(text) > 1 and text[-1] in "\\/" and not re.fullmatch(r"[A-Za-z]:[\\/]", text):
        text = text.rstrip("\\/")
    return text


def _path_edit(value, *, remove=(), prepend=()) -> str:
    """`value` without every entry that names a folder of `remove` or of
    `prepend` (each occurrence), with `prepend` in front, in order. Every
    other entry keeps its exact text (an unexpanded %VAR% included)."""
    drop = {_path_key(r) for r in remove} | {_path_key(p) for p in prepend}
    kept = [e for e in _path_entries(value) if _path_key(e) not in drop]
    return ";".join(list(prepend) + kept)


def _path_remove_later_duplicates(value, entry) -> str:
    """`value` with the later occurrences of `entry`'s folder dropped (the
    first one stays where it is)."""
    key, seen, out = _path_key(entry), False, []
    for e in _path_entries(value):
        if _path_key(e) == key:
            if seen:
                continue
            seen = True
        out.append(e)
    return ";".join(out)


def _path_replace(value, old, new) -> str:
    """`value` with each entry naming `old`'s folder replaced by `new`, at the
    same position."""
    key = _path_key(old)
    return ";".join(new if _path_key(e) == key else e for e in _path_entries(value))


def _new_process_path(store, values=None) -> List[Tuple[str, str]]:
    """(folder, "system"|"user") in the order a program started now gets
    them: Windows puts the system entries first, then the user's."""
    values = values or {s: store.read(s) for s in ("system", "user")}
    return [(_expand_entry(e), scope) for scope in ("system", "user")
            for e in _path_entries(values[scope][0])]


def _ansi_safe(text) -> bool:
    """True when `text` survives the ANSI code page (always off Windows)."""
    if os.name != "nt":
        return True
    try:
        str(text).encode("mbcs", "strict")
        return True
    except UnicodeEncodeError:
        return False


def _short_path(path) -> Optional[str]:
    """The 8.3 short name of an existing path, or None (off Windows, or on a
    volume without short names)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
    except Exception:
        return None
    return buf.value if 0 < n < len(buf) else None


# --- Tools on the PATH ------------------------------------------------------

@dataclass(frozen=True)
class _ToolUnit:
    key: str
    label: str
    programs: Tuple[str, ...]
    version_args: Tuple[str, ...]
    required: bool


_SETUP_UNITS = (
    # One unit: the three from one folder and one version (D5, #669).
    _ToolUnit("libjxl", "libjxl", ("cjxl", "djxl", "jxlinfo"), ("--version",), True),
    # One program, three names, tried in this order by the scripts.
    _ToolUnit("exiftool", "ExifTool", ("exiftool", "exiftool-k", "exiftool(-k)"), ("-ver",), True),
    _ToolUnit("magick", "ImageMagick", ("magick",), ("--version",), False),
)


def _setup_unit(key: str) -> _ToolUnit:
    return next(u for u in _SETUP_UNITS if u.key == key)


def _program_args(unit: _ToolUnit, program: str) -> Tuple[str, ...]:
    # jxlinfo has no --version: -h is its only option that exits 0 without a file.
    return ("-h",) if program == "jxlinfo" else unit.version_args


@dataclass
class _Probe:
    ok: bool
    rc: Optional[int]
    out: str
    err: str
    timed_out: bool = False
    error: str = ""


def _probe(path, args, timeout=20) -> _Probe:
    """Run a tool the way the scripts do: stdin NUL (an exiftool(-k)'s pause
    at its end returns at once), no shell."""
    try:
        r = subprocess.run([str(path), *args], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return _Probe(False, None, "", "", True, f"no answer in {timeout} s")
    except (OSError, ValueError) as e:
        return _Probe(False, None, "", "", False, str(e))
    return _Probe(r.returncode == 0, r.returncode, r.stdout or "", r.stderr or "", False,
                  "" if r.returncode == 0 else f"exit code {r.returncode}")


def _probe_error(p: _Probe) -> str:
    said = _common._stderr_tail(p.err or p.out or "")
    return f"{p.error}: {said}" if said and not p.timed_out else (p.error or said or "failed")


def _pathext() -> List[str]:
    return [e for e in (os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(";") if e]


# What Windows starts for a bare name ("cjxl"): CreateProcess adds .exe only,
# never PATHEXT — a cjxl.cmd is found by `where` and by shutil.which, and no
# script can start it. (The suite's fake tools are .cmd files: its fixture
# widens this.)
_SETUP_STARTABLE_EXTS = (".exe",)


def _startable(path) -> bool:
    """Can the scripts start this copy by its bare name?"""
    return os.name != "nt" or str(path).lower().endswith(_SETUP_STARTABLE_EXTS)


def _prepath_folders() -> List[Tuple[str, str]]:
    """(folder, "python"|"current") where Windows looks for a bare name BEFORE
    the PATH: the folder of the program that is running (python.exe — the
    base one under a virtual environment: it is the process), then the
    current folder. Empty off Windows."""
    if os.name != "nt":
        return []
    out = []
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetModuleFileNameW(None, buf, len(buf))
        exe = buf.value if 0 < n < len(buf) else sys.executable
    except Exception:
        exe = sys.executable
    if exe:
        out.append((os.path.dirname(exe), "python"))
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    if cwd and _path_key(cwd) not in {_path_key(f) for f, _s in out}:
        out.append((cwd, "current"))
    return out


def _find_copies(name: str, dirs) -> List[Path]:
    """Like `where`: in each folder, in order, `name` with each PATHEXT
    extension (and `name` itself when it already ends in one); one entry per
    real file."""
    out, seen = [], set()
    if os.name == "nt":
        exts = _pathext()
        names = [name + e for e in exts]
        if any(name.lower().endswith(e.lower()) for e in exts):
            names.insert(0, name)
    else:
        names = [name]
    for d in dirs:
        if not d:
            continue
        listing = None
        for n in names:
            p = os.path.join(d, n)
            try:
                if not os.path.isfile(p):
                    continue
                if os.name != "nt" and not os.access(p, os.X_OK):
                    continue
                key = os.path.normcase(os.path.realpath(p))
                if os.name == "nt":
                    # The name as the folder writes it (cjxl.exe, not PATHEXT's cjxl.EXE).
                    if listing is None:
                        listing = {x.lower(): x for x in os.listdir(d)}
                    p = os.path.join(d, listing.get(n.lower(), n))
            except (OSError, ValueError):
                continue
            if key not in seen:
                seen.add(key)
                out.append(Path(p))
    return out


def _parse_version(unit_key: str, program: str, p: _Probe):
    """(version text, version tuple, quantum) from a probe; Nones when unknown."""
    text = f"{p.out}\n{p.err}"
    if unit_key == "libjxl":
        if program == "jxlinfo":
            return None, None, None
        m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text)
        return ((f"{m.group(1)}.{m.group(2)}.{m.group(3)}", tuple(int(x) for x in m.groups()), None)
                if m else (None, None, None))
    if unit_key == "exiftool":
        m = re.search(r"(\d+)\.(\d+)", text)
        return (m.group(0), tuple(int(x) for x in m.groups()), None) if m else (None, None, None)
    m = re.search(r"ImageMagick\s+(\d+)\.(\d+)\.(\d+)(?:-(\d+))?\s+(Q\d+)", text)
    if not m:
        return None, None, None
    nums = [int(x) for x in m.groups()[:3]] + [int(m.group(4) or 0)]
    version = f"{m.group(1)}.{m.group(2)}.{m.group(3)}" + (f"-{m.group(4)}" if m.group(4) else "")
    return version, tuple(nums), m.group(5)


@dataclass
class _Copy:
    program: str
    path: str
    folder: str
    scope: str
    probe: Optional[_Probe] = None
    version: Optional[str] = None
    vtuple: Optional[tuple] = None
    quantum: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.probe and self.probe.ok)

    @property
    def real(self) -> str:
        return os.path.normcase(os.path.realpath(self.path))


@dataclass
class _UnitState:
    unit: _ToolUnit
    copies: Dict[str, List[_Copy]] = field(default_factory=dict)
    winners: Dict[str, Optional[_Copy]] = field(default_factory=dict)

    @property
    def main(self) -> Optional[_Copy]:
        """The copy whose version and folder the table shows."""
        return self.winners.get(self.unit.programs[0])

    def all_copies(self) -> List[_Copy]:
        return [c for p in self.unit.programs for c in self.copies.get(p, [])]


@dataclass
class _Finding:
    severity: str            # error | warning | info
    code: str
    unit: str
    message: str
    fix: Optional[Tuple] = None


@dataclass
class _SetupReport:
    values: Dict[str, Tuple[Optional[str], Optional[int]]]
    view: List[Tuple[str, str]]
    units: Dict[str, _UnitState]
    findings: List[_Finding]


def _locate(program: str, view) -> List[Tuple[Path, str, str]]:
    """Every copy of `program` on the new-process PATH: (file, folder, scope),
    in PATH order, one per real file."""
    out, seen = [], set()
    for folder, scope in view:
        for p in _find_copies(program, [folder]):
            key = os.path.normcase(os.path.realpath(str(p)))
            if key not in seen:
                seen.add(key)
                out.append((p, folder, scope))
    return out


def _setup_protected(folder: str, units: Dict[str, _UnitState]) -> Optional[str]:
    """Why a folder's PATH entry is never offered for removal, or None: the
    Windows folder (System32 and its kin hold everything) or a folder that
    holds the copy another tool runs."""
    key = _path_key(folder)
    root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if root and (key == _path_key(root) or key.startswith(_path_key(root) + os.sep)):
        return "it is a Windows folder"
    for st in units.values():
        for c in st.winners.values():
            if c and _path_key(c.folder) == key:
                return f"it holds the {c.program} that runs"
    return None


def _versions_text(copies: List[_Copy]) -> str:
    return ", ".join(sorted({c.version or "?" for c in copies}))


def _not_an_exe_text(path, prog) -> str:
    """Why a copy that is no .exe cannot be the tool (the diagnosis and the
    folder check say it in the same words)."""
    return (f"{path} is not an .exe: the scripts start `{prog}` directly, and Windows "
            f"runs only an .exe that way (a .cmd or .bat is found by `where`, never "
            f"started).")


def _unit_findings(st: _UnitState, units: Dict[str, _UnitState]) -> List[_Finding]:
    u, out = st.unit, []
    tested = _SETUP_TESTED_TOOLS[u.key]["version"]

    def add(sev, code, msg, fix=None):
        out.append(_Finding(sev, code, u.key, msg, fix))

    def unstartable(programs):
        """The first copy of these programs that exists but is no .exe."""
        return next((c for p in programs for c in st.copies.get(p, [])
                     if not _startable(c.path)), None)

    setf = ("set_folder", u.key)
    if u.key == "libjxl":
        missing = [p for p in u.programs if st.winners.get(p) is None]
        present = [st.winners[p] for p in u.programs if st.winners.get(p)]
        _still_missing = []
        for p in missing:
            c = unstartable((p,))
            if c:
                add("error", "NOT_AN_EXE", _not_an_exe_text(c.path, p), setf)
            else:
                _still_missing.append(p)
        missing = _still_missing
        if missing:
            add("error", "MISSING", f"{', '.join(missing)} not found on the PATH a new program "
                f"gets.", setf)
        for c in present:
            if not c.ok:
                add("error", "BROKEN", f"{c.path} does not run ({_probe_error(c.probe)}).", setf)
        if len({_path_key(c.folder) for c in present}) > 1:
            add("error", "LIBJXL_SPLIT", "cjxl, djxl and jxlinfo come from different folders ("
                + "; ".join(f"{c.program}: {c.folder}" for c in present)
                + "): they must be one libjxl, of one version (#669: a cjxl 0.13 archive "
                  "rebuilt by a djxl 0.12 is another JPEG).", setf)
        cj, dj = st.winners.get("cjxl"), st.winners.get("djxl")
        if cj and dj and cj.vtuple and dj.vtuple and cj.vtuple != dj.vtuple:
            add("error", "LIBJXL_VERSIONS", f"cjxl is {cj.version} ({cj.folder}) and djxl is "
                f"{dj.version} ({dj.folder}): one libjxl, of one version, is needed (#669).", setf)
        old = [c for c in (cj, dj) if c and c.ok and c.vtuple and c.vtuple < _SETUP_MIN_LIBJXL]
        if old:
            add("error", "TOO_OLD", f"{old[0].program} is libjxl {old[0].version}: the toolkit "
                f"needs {'.'.join(map(str, _SETUP_MIN_LIBJXL))} or newer.", setf)
        runs = all(st.winners.get(p) and st.winners[p].ok for p in u.programs)
        if runs and not out and (cj.version != tested or dj.version != tested):
            add("info", "UNTESTED", f"libjxl {cj.version or 'of unknown version'} runs; this "
                f"release was tested with {tested}.",
                ("download", u.key) if os.name == "nt" else None)
    else:
        w = st.main
        c = unstartable(u.programs) if w is None else None
        if c is not None:
            _msg = _not_an_exe_text(c.path, c.program)
            if u.key == "exiftool" and any(_startable(x.path) for x in st.all_copies()):
                _msg += (" The scripts take the first of exiftool, exiftool-k, exiftool(-k) "
                         "that exists: remove this file, or the working copy is never "
                         "reached.")
            add("error" if u.key == "exiftool" else "warning", "NOT_AN_EXE", _msg, setf)
        elif w is None:
            if u.key == "exiftool":
                add("error", "MISSING", "ExifTool (exiftool, exiftool-k or exiftool(-k)) not "
                    "found on the PATH a new program gets.", setf)
            else:
                add("warning", "MISSING", "magick not found: ICC profile conversions are off "
                    "(the wizard says 'ICC off').", setf)
        elif not w.ok:
            if u.key == "exiftool" and not _ansi_safe(w.folder):
                short = _short_path(w.folder)
                fix = (("short_path", w.folder) if short and _ansi_safe(short)
                       else (("download", u.key) if os.name == "nt" else setf))
                add("error", "EXIFTOOL_ANSI", f"{w.path} does not run ({_probe_error(w.probe)}): "
                    f"its folder has characters outside the Windows code page, where ExifTool's "
                    f"launcher cannot find its exiftool_files.", fix)
            else:
                add("error", "BROKEN", f"{w.path} does not run ({_probe_error(w.probe)}).", setf)
        elif u.key == "exiftool":
            if w.vtuple and w.vtuple < _SETUP_MIN_EXIFTOOL:
                add("warning", "OLD", f"ExifTool {w.version} works, but below "
                    f"{'.'.join(map(str, _SETUP_MIN_EXIFTOOL))} it runs one process per "
                    f"command (slower).", setf)
        else:
            if w.vtuple and w.vtuple[0] < _SETUP_MIN_MAGICK_MAJOR:
                add("warning", "OLD", f"ImageMagick {w.version}: the toolkit needs version "
                    f"{_SETUP_MIN_MAGICK_MAJOR} (ICC off otherwise).", setf)
            if w.quantum == "Q8":
                add("error", "Q8", f"ImageMagick {w.version} is a Q8 build: 16-bit precision is "
                    f"lost. Use a Q16 (Q16-HDRI) build.", setf)
        if w is not None and w.ok and not out and w.version != tested:
            add("info", "UNTESTED", f"{u.label} {w.version or 'of unknown version'} runs; this "
                f"release was tested with {tested}.",
                ("download", u.key) if os.name == "nt" else None)

    # Copies that lose: to a system copy (only the administrator can fix
    # that), or plain duplicates.
    shadowed_folders, conflicts = {}, {}
    names = (("exiftool",) if u.key == "exiftool" else u.programs)
    for prog in names:
        w = st.winners.get(prog)
        if w is None:
            continue
        pool = st.all_copies() if u.key == "exiftool" else st.copies.get(prog, [])
        for c in pool:
            if c.real == w.real:
                continue
            if w.scope == "system" and c.scope == "user":
                shadowed_folders.setdefault(w.folder, []).append((w, c))
            else:
                conflicts.setdefault(c.folder, []).append((w, c))
    for folder, pairs in shadowed_folders.items():
        why = _setup_protected_for_system(folder)
        others = [] if why else _programs_besides(folder, u.key)
        add("warning", "SHADOWED_BY_SYSTEM",
            "; ".join(f"{c.path} ({c.version or '?'}, user PATH) loses to {w.path} "
                      f"({w.version or '?'}, system PATH)" for w, c in pairs)
            + ": Windows puts the system PATH first."
            + (f" Its system entry is not offered for removal: {why}." if why else "")
            + (f" Its folder also holds {_programs_listing(others)}: they would leave the "
               f"PATH with it." if others else ""),
            None if why else ("remove_entries", "system", [folder]))
    for folder, pairs in conflicts.items():
        same = all(c.version == w.version for w, c in pairs)
        scope = pairs[0][1].scope
        why = _setup_protected(folder, units)
        if why is None and scope == "user":
            # The losing copy does no harm; the folder's other programs would
            # leave the PATH with it (a shims or tools folder): [r] only.
            others = _programs_besides(folder, u.key)
            if others:
                why = (f"it also holds {_programs_listing(others)} ([r] in the tool's menu "
                       f"removes it anyway)")
        fix = (("remove_entries", "user", [folder]) if scope == "user" and not why else None)
        add("info" if same else "warning", "CONFLICT",
            "; ".join(f"{c.path} ({c.version or '?'}, {c.scope} PATH) is not used: {w.path} "
                      f"({w.version or '?'}) comes first" for w, c in pairs)
            + (f". Not offered for removal: {why}." if why and scope == "user" else "."), fix)
    return out


def _programs_besides(folder: str, unit_key: Optional[str] = None) -> List[str]:
    """The programs in `folder` other than the unit's (every setup tool's
    when no unit): what else leaves the PATH with the folder's entry."""
    units = [u for u in _SETUP_UNITS if unit_key in (None, u.key)]
    own = {p.lower() for u in units for p in u.programs}
    try:
        return sorted(p.name for p in Path(folder).iterdir()
                      if p.is_file() and p.suffix.lower() in (".exe", ".com", ".cmd", ".bat")
                      and p.stem.lower() not in own)
    except OSError:
        return []


def _programs_listing(names: List[str]) -> str:
    return ", ".join(names[:12]) + (" ..." if len(names) > 12 else "")


def _setup_protected_for_system(folder: str) -> Optional[str]:
    root = os.environ.get("SystemRoot") or os.environ.get("windir")
    key = _path_key(folder)
    if root and (key == _path_key(root) or key.startswith(_path_key(root) + os.sep)):
        return "it is a Windows folder"
    return None


def _entry_findings(values, units) -> List[_Finding]:
    out = []
    for scope in ("user", "system"):
        seen, repeated = set(), set()
        for e in _path_entries(values[scope][0]):
            key = _path_key(e)
            if key in seen:
                if key not in repeated:
                    repeated.add(key)
                    out.append(_Finding("info", "DUPLICATE_ENTRY", "path",
                                        f"{e} is in the {scope} PATH more than once.",
                                        ("remove_duplicates", scope, e)))
                continue
            seen.add(key)
            folder = _expand_entry(e)
            if re.search(r"%[A-Za-z_][A-Za-z0-9_()]*%", folder):
                # A variable this process does not know (set since it
                # started?): cannot tell. A folder named "100%off %d" is a
                # name, not a variable, and is judged.
                continue
            try:
                exists = os.path.isdir(folder)
            except (OSError, ValueError):
                exists = True
            if not exists:
                out.append(_Finding(
                    "warning" if scope == "user" else "info", "STALE_ENTRY", "path",
                    f"{e} ({scope} PATH): the folder does not exist (a drive that is not "
                    f"connected looks the same: then keep it).",
                    ("remove_entries", scope, [e])))
    return out


def _process_only_findings(units, view, process_path) -> List[_Finding]:
    out = []
    dirs = [d for d in (process_path or "").split(os.pathsep) if d.strip()]
    for st in units.values():
        names = st.unit.programs
        if any(st.winners.get(p) for p in names):
            continue
        for prog in names:
            found = [f for f in _find_copies(prog, [_expand_entry(d) for d in dirs])
                     if _startable(f)]
            if found:
                out.append(_Finding(
                    "info", "PROCESS_ONLY", st.unit.key,
                    f"{found[0]} is on this window's PATH only (a launcher or a script added "
                    f"it): a program started from elsewhere, a scheduled task included, will "
                    f"not find it.", None))
                break
    return out


def _prepath_findings(units, view) -> List[_Finding]:
    """A copy Windows starts BEFORE anything on the PATH: next to the running
    python.exe (it wins for every script) or in the current folder (it wins
    for a script started from here). The table's winner is the PATH's; these
    lines say when another file runs instead. Never a fix: nothing here is a
    PATH entry."""
    out = []
    on_path = {_path_key(f) for f, _s in view}
    for folder, where in _prepath_folders():
        if _path_key(folder) in on_path:
            continue            # the PATH scan already judges that folder
        for st in units.values():
            names = st.unit.programs
            is_exiftool = st.unit.key == "exiftool"
            if is_exiftool:
                # Only the NAME the scripts start: the first with any copy on
                # the PATH (shutil.which), else each of the three in order.
                _chosen = next((n for n in names if st.copies.get(n)), None)
                names = (_chosen,) if _chosen else names
            for prog in names:
                found = [f for f in _find_copies(prog, [folder]) if _startable(f)]
                if not found:
                    continue
                w = st.winners.get("exiftool" if is_exiftool else prog)
                if w is not None and os.path.normcase(os.path.realpath(str(found[0]))) == w.real:
                    continue
                loses = (f"not {w.path}" if w is not None else "though the PATH has none")
                if where == "python":
                    out.append(_Finding(
                        "warning", "PYTHON_FOLDER_COPY", st.unit.key,
                        f"{found[0]} sits next to python.exe: Windows starts it before "
                        f"anything on the PATH, so the scripts run it, {loses}. Move or "
                        f"delete it by hand if that is not what you want.", None))
                else:
                    out.append(_Finding(
                        "info", "CURRENT_FOLDER_COPY", st.unit.key,
                        f"{found[0]} is in the current folder: a script started from "
                        f"here runs it, {loses}.", None))
                if is_exiftool:
                    break       # one finding per folder for ExifTool
    return out


def _diagnose(store, probe=_probe, process_path=None) -> _SetupReport:
    """What a program started now gets for each tool (system PATH, then the
    user's), every copy found, and the findings."""
    values = {s: store.read(s) for s in ("system", "user")}
    view = _new_process_path(store, values)
    cache: Dict[Tuple, _Probe] = {}
    units: Dict[str, _UnitState] = {}
    for unit in _SETUP_UNITS:
        st = _UnitState(unit)
        for prog in unit.programs:
            copies = []
            for path, folder, scope in _locate(prog, view):
                args = _program_args(unit, prog)
                key = (os.path.normcase(os.path.realpath(str(path))), args)
                if key not in cache:
                    cache[key] = probe(path, list(args))
                c = _Copy(prog, str(path), folder, scope, cache[key])
                c.version, c.vtuple, c.quantum = _parse_version(unit.key, prog, c.probe)
                copies.append(c)
            st.copies[prog] = copies
        def _first_startable(copies):
            return next((c for c in copies if _startable(c.path)), None)

        if unit.key == "exiftool":
            # The scripts try the NAMES in order, each over the whole PATH
            # (shutil.which: any PATHEXT copy picks the name), then start that
            # bare name: only an .exe of THAT name runs.
            _name = next((n for n in unit.programs if st.copies[n]), None)
            st.winners["exiftool"] = _first_startable(st.copies[_name]) if _name else None
        else:
            for prog in unit.programs:
                st.winners[prog] = _first_startable(st.copies[prog])
        units[unit.key] = st
    findings: List[_Finding] = []
    for st in units.values():
        findings += _unit_findings(st, units)
    findings += _entry_findings(values, units)
    findings += _process_only_findings(
        units, view, os.environ.get("PATH", "") if process_path is None else process_path)
    findings += _prepath_findings(units, view)
    findings.sort(key=lambda f: _SETUP_SEVERITY_ORDER[f.severity])
    return _SetupReport(values, view, units, findings)


# --- Python ------------------------------------------------------------------

def _python_report(run=None, which=None) -> Dict:
    """The interpreter, pip and the five packages, asked in processes of their
    own (never imported here)."""
    run = run or subprocess.run
    which = which or shutil.which
    rep = {"executable": sys.executable, "version": sys.version.split()[0],
           "venv": sys.prefix != sys.base_prefix, "prefix": sys.prefix,
           "pip": None, "packages": {}, "error": None, "py_launcher": None}

    def call(argv, timeout=120):
        try:
            return run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            return e

    r = call([sys.executable, "-m", "pip", "--version"])
    if not isinstance(r, Exception) and r.returncode == 0:
        words = (r.stdout or "").split()
        rep["pip"] = words[1] if len(words) > 1 else "?"
    r = call([sys.executable, "-c", _SETUP_PACKAGE_PROBE])
    try:
        if isinstance(r, Exception) or r.returncode != 0:
            raise ValueError(_common._stderr_tail(getattr(r, "stderr", "") or str(r)) or "failed")
        lines = (r.stdout or "").strip().splitlines()
        rep["packages"] = {k: v for k, v in json.loads(lines[-1]).items()}
    except (ValueError, IndexError, AttributeError) as e:
        rep["error"] = str(e) or "no answer"
    py = which("py")
    if py:
        r = call([py, "-c", "import sys; print(sys.executable)"], timeout=30)
        exe = "" if isinstance(r, Exception) else (r.stdout or "").strip()
        try:
            differs = bool(exe) and (os.path.normcase(os.path.realpath(exe))
                                     != os.path.normcase(os.path.realpath(sys.executable)))
        except (OSError, ValueError):
            differs = False
        if differs:
            rep["py_launcher"] = exe
    return rep


def _python_findings(py: Dict) -> List[_Finding]:
    out = []
    if py.get("error"):
        out.append(_Finding("warning", "PACKAGES_UNREAD", "python",
                            f"The package check did not answer ({py['error']}).", None))
    else:
        missing = [n for n in _SETUP_TESTED_PACKAGES if py["packages"].get(n) is None]
        if missing:
            sev = "error" if any(n in _SETUP_PACKAGES_REQUIRED for n in missing) else "warning"
            out.append(_Finding(sev, "PACKAGES_MISSING", "python",
                                f"Python packages missing: {', '.join(missing)}.", ("packages",)))
    if not py.get("pip"):
        out.append(_Finding("warning", "NO_PIP", "python",
                            f"pip is not installed for {py['executable']}.", ("packages",)))
    if py.get("py_launcher"):
        out.append(_Finding(
            "info", "PY_LAUNCHER_DIFFERS", "python",
            f"`py` starts {py['py_launcher']}; packages installed here go to "
            f"{py['executable']}. Run the setup with that one too if you use it: "
            f"`py jxl_photo.py setup`.", None))
    return out


def _pip_command(kind: str, py: Dict) -> Optional[List[str]]:
    """[i] the missing packages, [u] all five upgraded, [t] the tested pins."""
    names = list(_SETUP_TESTED_PACKAGES)
    base = [sys.executable, "-m", "pip", "install"]
    if kind == "i":
        missing = [n for n in names if (py.get("packages") or {}).get(n) is None]
        return base + missing if missing else None
    if kind == "u":
        return base + ["--upgrade"] + names
    if kind == "t":
        return base + [f"{n}=={v}" for n, v in _SETUP_TESTED_PACKAGES.items()]
    return None


# --- The report --------------------------------------------------------------

def _fix_text(f: _Finding) -> Optional[str]:
    if not f.fix:
        return None
    kind = f.fix[0]
    menu = {"libjxl": "1", "exiftool": "2", "magick": "3"}.get(f.unit, "")
    if kind == "set_folder":
        return (f"[{menu}] then [p] another folder"
                + (", or [d] the tested download" if os.name == "nt" else ""))
    if kind == "download":
        return f"[{menu}] then [d] the tested download"
    if kind == "remove_entries":
        return (f"remove {'; '.join(f.fix[2])} from the {f.fix[1]} PATH"
                + (" (administrator)" if f.fix[1] == "system" else ""))
    if kind == "remove_duplicates":
        return f"keep only the first {f.fix[2]} in the {f.fix[1]} PATH"
    if kind == "short_path":
        return f"put the folder's short name ({_short_path(f.fix[1])}) on the PATH instead"
    if kind == "packages":
        return "[p] Python packages"
    return None


def _unit_status(st: _UnitState, findings: List[_Finding]) -> str:
    sev = {f.severity for f in findings if f.unit == st.unit.key}
    return "ERROR" if "error" in sev else ("warning" if "warning" in sev else "OK")


def _show_setup_report(ui, report: _SetupReport, py: Dict, findings: List[_Finding],
                       store=None) -> None:
    ui.section("Setup — tools and Python packages")
    ui.line(f"Python {py['version']}: {py['executable']}"
            + (f" (virtual environment: {py['prefix']})" if py["venv"] else ""))
    if isinstance(store, _JsonPathStore):
        ui.line(f"PATH values from {store.file} (JXLPHOTO_SETUP_PATH_STORE), not the registry.")
    rows = []
    for key in ("libjxl", "exiftool", "magick"):
        st = report.units[key]
        w = st.main
        rows.append((st.unit.label, (w.version or "?") if w else "—", w.folder if w else "—",
                     f"{w.scope} PATH" if w else "not found", _unit_status(st, findings)))
    tested = ", ".join(f"{_setup_unit(k).label} {v['version']}" for k, v in _SETUP_TESTED_TOOLS.items())
    ui.table("Tools", ["Tool", "Version", "Folder", "From", "Status"], rows,
             notes=[f"Tested with: {tested}."])
    prow = []
    for name, tested_v in _SETUP_TESTED_PACKAGES.items():
        v = None if py.get("error") else py["packages"].get(name)
        status = ("?" if py.get("error") else "missing" if v is None
                  else "OK" if v == tested_v else f"OK (tested: {tested_v})")
        prow.append((name, v or "—", status))
    ui.table("Python packages", ["Package", "Version", "Status"], prow,
             notes=[f"pip {py['pip']}" if py.get("pip") else "pip: not installed"])
    if not any(f.severity in ("error", "warning") for f in findings):
        ui.line("Everything works.")
    for i, f in enumerate(findings, 1):
        ui.line(f"{i}. {f.severity.upper():7} {f.code} ({f.unit}): {f.message}")
        fix = _fix_text(f)
        if fix:
            ui.line(f"   Fix: {fix}")


def _setup_check(store, probe=_probe, python_report=None, process_path=None) -> int:
    """--check: the report with plain print; nothing asked, nothing written."""
    report = _diagnose(store, probe, process_path)
    py = (python_report or _python_report)()
    findings = report.findings + _python_findings(py)
    _show_setup_report(PlainUI(), report, py, findings, store)
    return 1 if any(f.severity == "error" for f in findings) else 0


# --- Writing PATH values -----------------------------------------------------

def _setup_backup_dir(create: bool = True) -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    folder = Path(base) / "jxl-photo" / "path-backups"
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def _store_id(store) -> str:
    """What a backup was made against: the registry, or one JSON copy (a
    by-hand try with JXLPHOTO_SETUP_PATH_STORE writes its backups to the
    same folder; [z] on the registry must never restore one of those)."""
    if isinstance(store, _JsonPathStore):
        return "json:" + os.path.normcase(os.path.abspath(str(store.file)))
    return "registry" if isinstance(store, _PathStore) else type(store).__name__


def _write_backup(folder: Path, scope, regtype, before, after, reason,
                  store_id: str = "registry") -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path, n = folder / f"{stamp}_{scope}.json", 2
    while path.exists():
        path, n = folder / f"{stamp}_{scope}_{n}.json", n + 1
    data = {"scope": scope, "type": regtype, "before": before, "after": after,
            "reason": reason, "toolkit_version": TOOLKIT_VERSION, "store": store_id}
    with open(path, "x", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def _log_change(folder: Path, text: str) -> None:
    try:
        with open(folder / "changes.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def _write_path(store, scope, new_value, *, expected_before, expected_type, reason,
                say=print) -> bool:
    """Every PATH write: read again, compare-and-swap against what the
    diagnosis read, back up, write, broadcast, one line in changes.log. The
    system PATH goes through the elevated plan (D7)."""
    if scope == "system":
        if getattr(store, "elevation_needed", False):
            return _elevated_system_path_edit(store, new_value, expected_before, expected_type,
                                              reason, say=say)
        plan, sha = _write_system_plan(new_value, expected_before, expected_type, reason)
        try:
            code = _apply_system_path_plan(store, plan, sha)
            return _report_plan_result(code, plan, say)
        finally:
            shutil.rmtree(plan.parent, ignore_errors=True)
    try:
        now = store.read(scope)
    except OSError as e:
        say(f"Could not read the {scope} PATH again ({e}): nothing was written.")
        return False
    if now != (expected_before, expected_type):
        say(f"The {scope} PATH changed meanwhile (another program edited it): nothing was "
            f"written. Checking again.")
        return False
    regtype = expected_type if expected_type in (_REG_SZ, _REG_EXPAND_SZ) else _REG_EXPAND_SZ
    try:
        folder = _setup_backup_dir()
        backup = _write_backup(folder, scope, regtype, expected_before, new_value, reason,
                               _store_id(store))
    except OSError as e:
        say(f"Could not write the backup ({e}): nothing was written.")
        return False
    try:
        store.write(scope, new_value, regtype)
    except (OSError, NotImplementedError) as e:
        _log_change(folder, f"{scope} NOT written ({reason}): {e}")
        say(f"Writing the {scope} PATH failed ({e}): nothing was changed.")
        return False
    if not store.broadcast():
        say("Note: Windows could not be told about the change (the broadcast failed).")
    _log_change(folder, f"{scope} written ({reason}); backup {backup.name}")
    say(f"The {scope} PATH was written. Backup: {backup}")
    return True


def _write_system_plan(new_value, expected_before, expected_type, reason) -> Tuple[Path, str]:
    """The plan file the elevated step reads, and the SHA-256 of its bytes."""
    import hashlib
    folder = Path(tempfile.mkdtemp(prefix="jxlphoto_setup_"))
    plan = folder / "system_path_plan.json"
    data = json.dumps({"scope": "system", "new_value": new_value,
                       "expected_before": expected_before, "expected_type": expected_type,
                       "reason": reason, "backup_dir": str(_setup_backup_dir())},
                      indent=2, ensure_ascii=False).encode("utf-8")
    plan.write_bytes(data)
    # mkdtemp's folder lets in SYSTEM, Administrators and each file's OWNER
    # only (Python 3.12.4+): a result file the elevated step created would
    # belong to Administrators (or to another admin) and this user could not
    # read it. Created here, it stays this user's; the elevated step writes
    # into it.
    Path(str(plan) + ".result.json").write_text("{}", encoding="utf-8")
    return plan, hashlib.sha256(data).hexdigest()


def _plan_result(plan: Path) -> Dict:
    try:
        return json.loads(Path(str(plan) + ".result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _report_plan_result(code, plan: Path, say) -> bool:
    message = _plan_result(plan).get("message") or ""
    if code == 0:
        say(f"The system PATH was written ({message}).")
        return True
    if code == 3:
        say("The system PATH changed meanwhile: nothing was written. Checking again.")
    else:
        say(f"The system PATH was not changed (code {code}: {message or 'no answer'}).")
    return False


def _apply_system_path_plan(store, plan_file, sha256) -> int:
    """The elevated side (D7): asks nothing. 0 written, 3 changed meanwhile,
    4 SHA-256 mismatch or unreadable plan, 5 write failed; the answer also in
    <plan>.result.json."""
    import hashlib
    plan_file = Path(plan_file)
    result = Path(str(plan_file) + ".result.json")

    def done(code, message):
        try:
            result.write_text(json.dumps({"code": code, "message": message}), encoding="utf-8")
        except OSError:
            pass
        return code

    try:
        data = plan_file.read_bytes()
    except OSError as e:
        return done(4, f"the plan cannot be read ({e})")
    if not sha256 or hashlib.sha256(data).hexdigest() != str(sha256).strip().lower():
        return done(4, "the plan's SHA-256 does not match: nothing was written")
    try:
        plan = json.loads(data.decode("utf-8"))
        new_value, before = plan["new_value"], plan["expected_before"]
        btype, reason = plan["expected_type"], str(plan.get("reason") or "")
        if (plan.get("scope") != "system" or not isinstance(new_value, str)
                or not new_value.strip() or not (before is None or isinstance(before, str))
                or not (btype is None or (isinstance(btype, int) and not isinstance(btype, bool)))):
            raise ValueError("not a system PATH plan")
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError):
        return done(4, "the plan is not readable: nothing was written")
    try:
        now = store.read("system")
    except OSError as e:
        return done(5, f"the system PATH cannot be read ({e})")
    if now != (before, btype):
        return done(3, "the system PATH changed meanwhile: nothing was written")
    regtype = btype if btype in (_REG_SZ, _REG_EXPAND_SZ) else _REG_EXPAND_SZ
    try:
        folder = Path(plan["backup_dir"]) if plan.get("backup_dir") else _setup_backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        backup = _write_backup(folder, "system", regtype, before, new_value, reason,
                               _store_id(store))
    except (OSError, TypeError) as e:
        return done(5, f"the backup could not be written ({e}): nothing was written")
    try:
        store.write("system", new_value, regtype)
    except OSError as e:
        _log_change(folder, f"system NOT written ({reason}): {e}")
        return done(5, f"writing failed ({e})")
    store.broadcast()
    _log_change(folder, f"system written ({reason}); backup {backup.name}")
    return done(0, f"backup {backup}")


def _run_elevated(argv: List[str], wait: int = _SETUP_ELEVATED_WAIT):
    """Start argv as administrator (one UAC prompt) and wait for it:
    ("exited", code), ("declined", None), ("timeout", None) or ("failed", why)."""
    import ctypes
    from ctypes import wintypes

    class _SEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SEI)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    info = _SEI()
    info.cbSize = ctypes.sizeof(_SEI)
    info.fMask = 0x00000040 | 0x00008000      # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE
    info.lpVerb = "runas"
    info.lpFile = argv[0]
    info.lpParameters = subprocess.list2cmdline(argv[1:])
    info.nShow = 0                            # SW_HIDE: it asks nothing
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        return ("declined", None) if err == 1223 else ("failed", f"Windows error {err}")
    if not info.hProcess:
        return ("failed", "no process handle")
    try:
        if kernel32.WaitForSingleObject(info.hProcess, int(wait * 1000)) != 0:
            return ("timeout", None)
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            return ("failed", "no exit code")
        return ("exited", int(code.value))
    finally:
        kernel32.CloseHandle(info.hProcess)


def _elevated_system_path_edit(store, new_value, expected_before, expected_type, reason,
                               say=print) -> bool:
    """D7: the plan file, one elevated copy of this setup that applies it,
    its answer."""
    plan, sha = _write_system_plan(new_value, expected_before, expected_type, reason)
    try:
        how, code = _run_elevated([sys.executable, str(Path(__file__).resolve()), "setup",
                                   "--apply-system-path", str(plan), "--sha256", sha])
        if how == "declined":
            say("Not changed: the administrator prompt was declined.")
            return False
        if how == "timeout":
            say(f"Not confirmed: the administrator step did not end in "
                f"{_SETUP_ELEVATED_WAIT} s. Check again with [r].")
            return False
        if how != "exited":
            say(f"Not changed: the administrator step could not start ({code}).")
            return False
        return _report_plan_result(code, plan, say)
    finally:
        shutil.rmtree(plan.parent, ignore_errors=True)


def _setup_after_write(ui) -> None:
    """The wizard detects the tools again at its next start (--recheck)."""
    try:
        cfg = ConfigManager()
        cfg.config.dependencies_checked = False
        cfg.save_config()
    except Exception as e:
        ui.line(f"(The wizard's settings were not updated ({e}): start it with --recheck.)")
    ui.line(_SETUP_RESTART_NOTE)


def _show_path_diff(ui, store, scope, before, after) -> None:
    from collections import Counter
    b, a = _path_entries(before), _path_entries(after)
    bk, ak = [_path_key(x) for x in b], [_path_key(x) for x in a]
    cb, ca = Counter(bk), Counter(ak)
    ui.line(f"The {scope} PATH ({store.where.get(scope, scope)}):")
    changed = 0
    for i, (x, k) in enumerate(zip(a, ak), 1):
        if cb[k] == 0:
            ui.line(f"  + {x}   (new, position {i})")
            changed += 1
    for x, k in zip(b, bk):
        if ca[k] == 0:
            ui.line(f"  - {x}")
            changed += 1
    for k in dict.fromkeys(bk):
        if 0 < ca[k] < cb[k]:
            ui.line(f"  - {b[bk.index(k)]}   (a repeated entry: the first one stays)")
            changed += 1
    kept_b = [k for k in dict.fromkeys(bk) if ca[k]]
    kept_a = [k for k in dict.fromkeys(ak) if cb[k]]
    moved = 0
    for k in kept_a:
        if kept_a.index(k) < kept_b.index(k):
            ui.line(f"  ^ {a[ak.index(k)]}   (moved to position {ak.index(k) + 1})")
            moved += 1
    # The entries of the new value that are neither new nor moved (a removal
    # leaves them alone).
    ui.line(f"  {max(len(kept_a) - moved, 0)} other entries unchanged.")


def _confirm_and_write(ui, store, scope, before, before_type, after, reason) -> bool:
    """Show the change, ask, write through _write_path."""
    if _path_entries(after) == _path_entries(before):
        ui.line(f"The {scope} PATH already is that way: nothing to write.")
        return False
    if not getattr(store, "writable", True):
        added = [x for x in _path_entries(after)
                 if _path_key(x) not in {_path_key(y) for y in _path_entries(before)}]
        removed = [x for x in _path_entries(before)
                   if _path_key(x) not in {_path_key(y) for y in _path_entries(after)}]
        ui.line("This setup does not edit the PATH here. In your shell profile "
                "(~/.profile, ~/.bashrc or ~/.zshrc):")
        for x in added:
            ui.line(f'  export PATH="{x}:$PATH"')
        for x in removed:
            ui.line(f"  and remove {x} from the PATH set there.")
        return False
    _show_path_diff(ui, store, scope, before, after)
    if scope == "system":
        asked = ui.confirm("setup.elevate",
                           "Change the SYSTEM PATH? Windows asks for administrator rights "
                           "(one prompt)." if store.elevation_needed
                           else "Change the system PATH?", default=False)
    else:
        asked = ui.confirm("setup.confirm", "Write this user PATH?", default=False)
    if not asked:
        ui.line("Nothing was changed.")
        return False
    ok = _write_path(store, scope, after, expected_before=before, expected_type=before_type,
                     reason=reason, say=ui.line)
    if ok:
        _setup_after_write(ui)
    return ok


def _undo_last_change(store, ui) -> bool:
    """[z]: the PATH as it was before the newest change this setup made."""
    folder = _setup_backup_dir(create=False)
    try:
        backups = sorted((p for p in folder.glob("*.json") if p.is_file()),
                         key=lambda p: (p.stat().st_mtime_ns, p.name))
    except OSError:
        backups = []
    # The newest backup made against THIS store (the registry, or this JSON
    # copy): one of another store is never restored here.
    newest = data = None
    for candidate in reversed(backups):
        try:
            got = json.loads(candidate.read_text(encoding="utf-8"))
            if not isinstance(got, dict):
                raise ValueError("not a backup")
        except (OSError, ValueError) as e:
            ui.warn(f"The newest backup ({candidate}) cannot be read ({e}): nothing was "
                    f"changed.")
            return False
        if got.get("store", "registry") == _store_id(store):
            newest, data = candidate, got
            break
    if newest is None:
        ui.line("No PATH change made by this setup to undo.")
        return False
    try:
        scope, before, after = data["scope"], data["before"], data["after"]
        if scope not in ("user", "system"):
            raise ValueError(scope)
    except (ValueError, KeyError, TypeError) as e:
        ui.warn(f"The newest backup ({newest}) cannot be read ({e}): nothing was changed.")
        return False
    if before is None:
        ui.line(f"Before that change ({newest.name}) the {scope} PATH did not exist: remove "
                f"its entries with [r] in a tool's menu instead.")
        return False
    now, now_type = store.read(scope)
    _show_path_diff(ui, store, scope, now, before)
    if now == after:
        yes = ui.confirm("setup.undo", f"Restore the {scope} PATH as it was before "
                         f"{data.get('reason') or newest.name}?", default=True)
    else:
        ui.line(f"The {scope} PATH changed since that backup ({newest.name}): restoring it "
                f"also undoes those later changes.")
        yes = ui.confirm("setup.undo", "Restore it anyway?", default=False)
    if not yes:
        ui.line("Nothing was changed.")
        return False
    ok = _write_path(store, scope, before, expected_before=now, expected_type=now_type,
                     reason=f"undo of {newest.name}", say=ui.line)
    if ok:
        _setup_after_write(ui)
    return ok


# --- Actions -----------------------------------------------------------------

def _folder_from_answer(text: str) -> Path:
    path = Path(os.path.expanduser(_expand_env(text.strip().strip('"'))))
    if path.is_file():
        path = path.parent
    return Path(os.path.abspath(str(path)))


def _check_tool_folder(unit: _ToolUnit, folder: Path, probe=_probe):
    """(problem or None, {"version", "untested"} or None, ansi_problem): may
    this folder be the unit's?"""
    if not folder.is_dir():
        return f"{folder} is not a folder.", None, False
    if ";" in str(folder) or _expand_env(str(folder)) != str(folder):
        # Written into PATH, a ';' splits it in two entries, and %NAME% is
        # read as a variable: neither names this folder any more.
        return (f"{folder} cannot be a PATH entry (a ';' or a %NAME% in its path): move "
                f"the tool to another folder.", None, False)
    found = {}

    def _not_an_exe(path, prog):
        return _not_an_exe_text(path, prog), None, False

    if unit.key == "exiftool":
        # The NAME the scripts would take (the first with any copy), then an
        # .exe of that name: what Windows starts by the bare name.
        named = next(((n, c) for n in unit.programs for c in [_find_copies(n, [str(folder)])]
                      if c), None)
        if named is None:
            return (f"No ExifTool in {folder} (exiftool.exe, exiftool-k.exe or "
                    f"exiftool(-k).exe).", None, False)
        exe = next((x for x in named[1] if _startable(x)), None)
        if exe is None:
            return _not_an_exe(named[1][0], named[0])
        found["exiftool"] = exe
    else:
        missing = []
        for p in unit.programs:
            c = _find_copies(p, [str(folder)])
            if c and not any(_startable(x) for x in c):
                return _not_an_exe(c[0], p)
            c = [x for x in c if _startable(x)]
            if c:
                found[p] = c[0]
            else:
                missing.append(p)
        if missing:
            return (f"{', '.join(missing)} not in {folder}"
                    + (" (cjxl, djxl and jxlinfo must be in one folder)."
                       if unit.key == "libjxl" else "."), None, False)
    versions = {}
    for p, exe in found.items():
        pr = probe(exe, list(_program_args(unit, p)))
        if not pr.ok:
            if unit.key == "exiftool" and not _ansi_safe(str(folder)):
                return (f"{exe} does not run ({_probe_error(pr)}): its folder has characters "
                        f"outside the Windows code page, where ExifTool's launcher cannot find "
                        f"its exiftool_files.", None, True)
            return f"{exe} does not run ({_probe_error(pr)}).", None, False
        versions[p] = _parse_version(unit.key, p, pr)
    main = unit.programs[0] if unit.key != "exiftool" else "exiftool"
    version, vt, quantum = versions[main]
    if vt is None:
        return f"{found[main]} runs but gives no version.", None, False
    if unit.key == "libjxl":
        dv = versions["djxl"][1]
        if dv != vt:
            return (f"cjxl is {version} and djxl is {versions['djxl'][0]} there: one libjxl, of "
                    f"one version, is needed (#669).", None, False)
        if vt < _SETUP_MIN_LIBJXL:
            return (f"libjxl {version} is too old: {'.'.join(map(str, _SETUP_MIN_LIBJXL))} or "
                    f"newer is needed.", None, False)
    elif unit.key == "exiftool":
        if vt < _SETUP_MIN_EXIFTOOL:
            return (f"ExifTool {version} is older than "
                    f"{'.'.join(map(str, _SETUP_MIN_EXIFTOOL))}.", None, False)
    else:
        if vt[0] < _SETUP_MIN_MAGICK_MAJOR:
            return f"ImageMagick {version}: version {_SETUP_MIN_MAGICK_MAJOR} is needed.", None, False
        if quantum == "Q8":
            return f"ImageMagick {version} is a Q8 build: a Q16 one is needed.", None, False
    tested = _SETUP_TESTED_TOOLS[unit.key]["version"]
    return None, {"version": version, "untested": version != tested}, False


def _other_programs(folder: str, unit: _ToolUnit) -> List[str]:
    own = {p.lower() for p in unit.programs}
    try:
        return sorted(p.name for p in Path(folder).iterdir()
                      if p.is_file() and p.suffix.lower() in (".exe", ".cmd", ".bat")
                      and p.stem.lower() not in own)
    except OSError:
        return []


def _setup_set_folder(ui, store, report: _SetupReport, unit_key: str, probe=_probe,
                      folder=None) -> bool:
    """[p]: put a folder of the unit first on the user PATH (also to switch
    versions by choice). `folder` given (a download) skips the question."""
    unit = _setup_unit(unit_key)
    fixed = folder is not None
    while True:
        if fixed:
            cand = Path(folder)
        else:
            text = ui.ask_text("setup.folder", f"Folder of {unit.label} "
                               f"({', '.join(unit.programs)}), or a file in it (Enter = back)", "")
            if not text.strip():
                return False
            cand = _folder_from_answer(text)
        problem, info, ansi = _check_tool_folder(unit, cand, probe)
        if ansi:
            ui.warn(problem)
            short = _short_path(cand)
            if short and _ansi_safe(short):
                if ui.confirm("setup.confirm", f"Use its short name {short} instead?",
                              default=True):
                    problem, info, _ = _check_tool_folder(unit, Path(short), probe)
                    if problem:
                        ui.warn(problem)
                    else:
                        cand = Path(short)
            else:
                ui.line("This volume gives it no short name: download the tested version "
                        "([d]), which goes to a folder ExifTool can read.")
        elif problem:
            ui.warn(problem)
        if problem:
            if fixed:
                return False
            continue
        break
    tested = _SETUP_TESTED_TOOLS[unit.key]["version"]
    ui.line(f"{unit.label} {info['version']} in {cand}: it runs"
            + (" (the tested version)." if not info["untested"] else f" (tested: {tested})."))
    if info["untested"]:
        if not ui.confirm("setup.confirm", f"{unit.label} {info['version']} is not the version "
                          f"this release was tested with ({tested}). Use it anyway?",
                          default=False):
            ui.line("Nothing was changed.")
            return False
    new_entry = str(cand)
    before, btype = store.read("user")
    user_keys = {_path_key(e) for e in _path_entries(before)}
    st = report.units[unit.key]
    remove = []
    old_folders = []
    for c in st.winners.values():
        if (c and c.scope == "user" and _path_key(c.folder) != _path_key(new_entry)
                and _path_key(c.folder) in user_keys and c.folder not in old_folders):
            old_folders.append(c.folder)
    for old in old_folders:
        others = _other_programs(old, unit)
        listing = ", ".join(others[:12]) + (" ..." if len(others) > 12 else "")
        if ui.confirm("setup.remove_old",
                      f"Also remove the old folder {old} from the user PATH?"
                      + (f" It also holds: {listing}." if others
                         else " It holds no other program."), default=not others):
            remove.append(old)
    for c in st.winners.values():
        if c and c.scope == "system" and _path_key(c.folder) != _path_key(new_entry):
            ui.line(f"Note: {c.path} is on the SYSTEM PATH, which Windows puts first: it will "
                    f"still win. The check after this offers to remove its entry (administrator).")
            break
    after = _path_edit(before, remove=remove, prepend=[new_entry])
    return _confirm_and_write(ui, store, "user", before, btype, after,
                              reason=f"{unit.label} {info['version']}: {new_entry}")


def _setup_remove_entries(ui, store, scope, entries, units=None, strict=True,
                          unit_key=None) -> bool:
    """Remove PATH entries (every occurrence). A Windows folder never; with
    `strict` (a suggested fix) not a folder that holds a copy that runs
    either — [r] in a tool's menu, the person's own pick, only says so.
    The other programs each folder holds are named before the question."""
    before, btype = store.read(scope)
    after = _path_edit(before, remove=entries)
    for e in entries:
        why = _setup_protected_for_system(_expand_entry(e))
        if why is None and units is not None and scope == "user":
            held = _setup_protected(_expand_entry(e), units)
            if held and strict:
                why = held
            elif held:
                ui.line(f"Note: {e} — {held}.")
        if why:
            ui.line(f"{e} is not removed: {why}.")
            return False
    for e in entries:
        others = _programs_besides(_expand_entry(e), unit_key)
        if others:
            ui.line(f"{e} also holds {_programs_listing(others)}: they leave the PATH too.")
    return _confirm_and_write(ui, store, scope, before, btype, after,
                              reason=f"remove {'; '.join(entries)}")


def _setup_short_path(ui, store, folder) -> bool:
    short = _short_path(folder)
    if not short or not _ansi_safe(short):
        ui.line(f"{folder} has no short name ExifTool can read: download the tested version.")
        return False
    key = _path_key(folder)
    for scope in ("user", "system"):
        before, btype = store.read(scope)
        if any(_path_key(e) == key for e in _path_entries(before)):
            return _confirm_and_write(ui, store, scope, before, btype,
                                      _path_replace(before, folder, short),
                                      reason=f"short name of {folder}")
    ui.line(f"{folder} is not on the PATH any more.")
    return False


def _tools_base(arg, ui=None) -> Optional[Path]:
    """Where the tested downloads go: --tools-dir, else
    %LOCALAPPDATA%\\jxl-photo\\tools. ANSI-safe (ExifTool's launcher): its
    short name, else %SystemDrive%\\jxl-photo-tools, asked first."""
    if arg:
        base = Path(os.path.abspath(os.path.expanduser(_expand_env(str(arg)))))
    else:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / "jxl-photo" / "tools"
    if _ansi_safe(str(base)):
        return base
    base.mkdir(parents=True, exist_ok=True)
    short = _short_path(base)
    if short and _ansi_safe(short):
        return Path(short)
    fallback = Path((os.environ.get("SystemDrive") or "C:") + "\\") / "jxl-photo-tools"
    if ui is None or not ui.confirm(
            "setup.base_folder", f"{base} has characters ExifTool cannot read and no short "
            f"name. Put the downloads in {fallback} instead?", default=False):
        return None
    return fallback


def _clear_read_only(path) -> None:
    import stat
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if os.path.isdir(path) else 0))
    except OSError:
        pass


def _rmtree_force(path) -> None:
    """shutil.rmtree that also removes read-only entries: ExifTool's zip
    extracts its folders read-only, and Windows refuses to remove those."""
    def again(func, p, *_):
        _clear_read_only(p)
        try:
            func(p)
        except OSError:
            pass
    if not os.path.lexists(str(path)):
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=again)
    else:
        shutil.rmtree(path, onerror=again)


def _make_writable(root: Path) -> None:
    """Every file and folder of an installed copy writable, so it can be
    deleted later like any other folder (jxl-photo-exe does the same)."""
    for d, dirs, files in os.walk(root):
        _clear_read_only(d)
        for name in dirs + files:
            _clear_read_only(os.path.join(d, name))


def _first_in(root: Path, name: str) -> Path:
    found = sorted((p for p in root.rglob(name) if p.is_file()), key=lambda p: len(p.parts))
    if not found:
        raise FileNotFoundError(f"{name} is not in the archive")
    return found[0]


def _lay_out_tool(unit_key: str, src: Path, dest: Path) -> None:
    """The archive's files as jxl-photo-exe's build.py lays them out."""
    if unit_key == "libjxl":
        for exe in ("cjxl.exe", "djxl.exe", "jxlinfo.exe"):
            shutil.copy2(_first_in(src, exe), dest / exe)
        lic = next((p for p in src.rglob("licenses") if p.is_dir()), None)
        if lic is not None:
            shutil.copytree(lic, dest / "licenses")
    elif unit_key == "exiftool":
        k = _first_in(src, "exiftool(-k).exe")
        # Renamed: exiftool(-k).exe waits for a key after every call.
        shutil.copy2(k, dest / "exiftool.exe")
        shutil.copytree(k.parent / "exiftool_files", dest / "exiftool_files")
        if (k.parent / "README.txt").is_file():
            shutil.copy2(k.parent / "README.txt", dest / "README.txt")
    else:
        m = _first_in(src, "magick.exe")
        shutil.copy2(m, dest / "magick.exe")
        for f in m.parent.iterdir():
            if f.is_file() and (f.suffix.lower() in (".xml", ".icc")
                                or f.name in ("LICENSE.txt", "NOTICE.txt")):
                shutil.copy2(f, dest / f.name)


def _download_tested(unit, base, ui, probe=_probe, opener=None) -> Optional[Path]:
    """Part B: the tested version of a unit into <base>\\<unit>-<version>,
    its SHA-256 checked before anything is extracted or run. Returns that
    folder, or None. The PATH question is the caller's (_setup_set_folder)."""
    import hashlib
    import urllib.request
    unit = _setup_unit(unit) if isinstance(unit, str) else unit
    spec = _SETUP_TESTED_TOOLS[unit.key]
    base = Path(base)
    vdir = base / f"{unit.key}-{spec['version']}"
    if vdir.is_dir():
        problem, info, _ = _check_tool_folder(unit, vdir, probe)
        if not problem and info["version"] == spec["version"]:
            ui.line(f"{unit.label} {spec['version']} is already in {vdir}: using it.")
            return vdir
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(spec["url"], headers={"User-Agent": "jxl-photo-setup"})
    part, archive = base / (spec["archive"] + ".part"), base / spec["archive"]
    try:
        response = opener(request, timeout=300)
    except Exception as e:
        ui.warn(f"Could not reach {spec['url']} ({e}): nothing was downloaded.")
        return None
    digest = hashlib.sha256()
    try:
        try:
            size = int((getattr(response, "headers", None) or {}).get("Content-Length") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > _SETUP_MAX_DOWNLOAD:
            ui.warn(f"The server announces {size / 1e6:.0f} MB, more than the "
                    f"{_SETUP_MAX_DOWNLOAD // (1024 * 1024)} MB this setup takes: nothing was "
                    f"downloaded.")
            return None
        if not ui.confirm("setup.download",
                          f"Download {unit.label} {spec['version']} "
                          f"({f'{size / 1e6:.1f} MB' if size else 'size not given'}) from "
                          f"{spec['url']} into {base}? Its SHA-256 is checked before anything "
                          f"is extracted or run.", default=False):
            ui.line("Nothing was downloaded.")
            return None
        base.mkdir(parents=True, exist_ok=True)
        done, shown_at, shown_pct = 0, time.monotonic(), 0
        try:
            with open(part, "wb") as f:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > _SETUP_MAX_DOWNLOAD:
                        raise OSError(f"more than {_SETUP_MAX_DOWNLOAD // (1024 * 1024)} MB")
                    f.write(chunk)
                    digest.update(chunk)
                    pct = int(done * 100 / size) if size else 0
                    if time.monotonic() - shown_at >= 2 or (size and pct >= shown_pct + 5):
                        ui.line(f"  {done / 1e6:.1f} MB" + (f" of {size / 1e6:.1f} MB ({pct} %)"
                                                            if size else ""))
                        shown_at, shown_pct = time.monotonic(), pct
            if size and done != size:
                raise OSError(f"incomplete: {done} of {size} bytes")
        except Exception as e:
            part.unlink(missing_ok=True)
            ui.warn(f"The download failed ({e}): nothing was installed.")
            return None
    finally:
        try:
            response.close()
        except Exception:
            pass
    got = digest.hexdigest()
    if got != spec["sha256"]:
        part.unlink(missing_ok=True)
        ui.warn(f"SHA-256 mismatch: got {got}, expected {spec['sha256']}. The file was deleted; "
                f"nothing was run or installed.")
        return None
    ui.line(f"SHA-256 checked: {got}.")
    os.replace(part, archive)
    tar = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "tar.exe"
    if not tar.is_file():
        ui.warn(f"No {tar} to extract {archive}: extract it yourself and use [p] with the "
                f"folder.")
        return None
    stage = vdir.with_name(vdir.name + ".tmp")
    # A name no "<unit>-<version>" listing below can take for a version.
    extract = Path(tempfile.mkdtemp(prefix=f".extract-{unit.key}-", dir=str(base)))
    try:
        r = subprocess.run([str(tar), "-xf", str(archive), "-C", str(extract)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        if r.returncode != 0:
            ui.warn(f"tar could not extract {archive.name} "
                    f"({_common._stderr_tail(r.stderr or r.stdout)}): nothing was installed.")
            return None
        _rmtree_force(stage)
        stage.mkdir()
        _lay_out_tool(unit.key, extract, stage)
        (stage / "VERSION.txt").write_text(f"{spec['version']}\n{spec['sha256']}\n",
                                           encoding="utf-8")
        _make_writable(stage)
        if vdir.exists():
            aside = vdir.with_name(f"{vdir.name}.old-{time.strftime('%Y%m%d_%H%M%S')}")
            os.replace(vdir, aside)
            ui.line(f"The copy there that did not work was moved to {aside}.")
        os.replace(stage, vdir)
    except (OSError, subprocess.SubprocessError) as e:
        ui.warn(f"Installing failed ({e}): nothing was installed.")
        return None
    finally:
        _rmtree_force(extract)
        _rmtree_force(stage)
        archive.unlink(missing_ok=True)
    problem, info, _ = _check_tool_folder(unit, vdir, probe)
    if problem:
        ui.warn(f"The installed copy does not work: {problem}")
        return None
    ui.line(f"{unit.label} {spec['version']} installed in {vdir}.")
    older = sorted(str(p) for p in base.glob(f"{unit.key}-*")
                   if p.is_dir() and p != vdir and not p.name.endswith(".tmp")
                   and (p / "VERSION.txt").is_file())
    if older:
        ui.line("Other versions stay where they are (to go back: [p] with one of them): "
                + "; ".join(older))
    return vdir


def _setup_unit_menu(ui, store, report: _SetupReport, unit_key: str, args, probe=_probe,
                     opener=None) -> None:
    unit = _setup_unit(unit_key)
    st = report.units[unit_key]
    copies = st.all_copies()
    ui.section(unit.label)
    if not copies:
        ui.line("No copy on the PATH a new program gets.")
    for c in copies:
        won = any(w is not None and w.real == c.real for w in st.winners.values())
        ui.line(f"  {c.path}  ({c.scope} PATH, {c.version or 'no version'}, "
                f"{'runs' if c.ok else 'does not run'}{', used' if won else ''})")
    options = [("p", "Use another folder (also to switch versions)")]
    if os.name == "nt":
        options.append(("d", f"Download the tested version "
                             f"({_SETUP_TESTED_TOOLS[unit_key]['version']})"))
    options += [("r", "Remove a copy from the PATH"), ("0", "Back")]
    choice = ui.choose("setup.unit", unit.label, options, default="0")
    if choice == "p":
        _setup_set_folder(ui, store, report, unit_key, probe)
    elif choice == "d":
        base = _tools_base(getattr(args, "tools_dir", None), ui)
        if base is None:
            ui.line("Nothing was downloaded.")
            return
        folder = _download_tested(unit, base, ui, probe, opener)
        if folder is not None:
            _setup_set_folder(ui, store, report, unit_key, probe, folder=folder)
    elif choice == "r":
        folders = []
        for c in copies:
            if (c.folder, c.scope) not in folders:
                folders.append((c.folder, c.scope))
        if not folders:
            return
        picks = [(str(i), f"{f} ({s} PATH)") for i, (f, s) in enumerate(folders, 1)]
        pick = ui.choose("setup.unit", "Remove which folder from the PATH?",
                         picks + [("0", "Back")], default="0")
        if pick == "0":
            return
        folder, scope = folders[int(pick) - 1]
        _setup_remove_entries(ui, store, scope, [folder], report.units, strict=False,
                              unit_key=unit_key)


def _setup_packages(ui, py: Dict, run=None) -> None:
    """[p]: pip install, shown first, asked, run without capture (pip shows
    its own progress)."""
    run = run or subprocess.run
    if py.get("venv"):
        ui.line(f"A virtual environment: the packages go into {py['prefix']}.")
    if not py.get("pip"):
        cmd = [sys.executable, "-m", "ensurepip", "--upgrade"]
        ui.line("pip is not installed for this Python.")
        ui.line("Command: " + subprocess.list2cmdline(cmd))
        how = (f"Reinstall Python from python.org with 'pip' ticked, or run: "
               f"{sys.executable} -m ensurepip")
        if ui.confirm("setup.confirm", "Install pip with it?", default=False):
            r = run(cmd)
            if getattr(r, "returncode", 1) != 0:
                ui.warn(how)
        else:
            ui.line(how)
        return
    choice = ui.choose("setup.packages", "Python packages",
                       [("i", "Install the missing ones"), ("u", "Upgrade all five"),
                        ("t", "The tested versions ("
                         + ", ".join(f"{n} {v}" for n, v in _SETUP_TESTED_PACKAGES.items()) + ")"),
                        ("0", "Back")], default="0")
    if choice == "0":
        return
    cmd = _pip_command(choice, py)
    if not cmd:
        ui.line("Nothing is missing.")
        return
    ui.line("Command: " + subprocess.list2cmdline(cmd))
    if not ui.confirm("setup.confirm", "Run it?", default=False):
        ui.line("Nothing was installed.")
        return
    r = run(cmd)
    code = getattr(r, "returncode", 1)
    ui.line("pip finished." if code == 0 else f"pip ended with exit code {code}.")


def _setup_fix_all(ui, store, args, report: _SetupReport, findings, probe=_probe,
                   opener=None, py=None, pip_run=None) -> None:
    """[f]: each suggested fix in turn, each with its own question."""
    opened = set()
    for f in findings:
        kind = f.fix[0]
        ui.line(f"{f.severity.upper()} {f.code} ({f.unit}): {f.message}")
        if kind in ("set_folder", "download"):
            if f.unit in opened:
                continue
            opened.add(f.unit)
            _setup_unit_menu(ui, store, report, f.unit, args, probe, opener)
        elif kind == "remove_entries":
            _setup_remove_entries(ui, store, f.fix[1], list(f.fix[2]), report.units,
                                  unit_key=f.unit if f.unit in ("libjxl", "exiftool", "magick")
                                  else None)
        elif kind == "remove_duplicates":
            before, btype = store.read(f.fix[1])
            _confirm_and_write(ui, store, f.fix[1], before, btype,
                               _path_remove_later_duplicates(before, f.fix[2]),
                               reason=f"repeated entry {f.fix[2]}")
        elif kind == "short_path":
            _setup_short_path(ui, store, f.fix[1])
        elif kind == "packages":
            if "packages" in opened:
                continue
            opened.add("packages")
            _setup_packages(ui, py or {}, pip_run)


def _setup_session(ui, store, args, probe=_probe, python_report=None, pip_run=None,
                   opener=None, process_path=None) -> int:
    """The interactive setup: the report, then the fixes the person picks."""
    while True:
        report = _diagnose(store, probe, process_path)
        py = (python_report or _python_report)()
        findings = report.findings + _python_findings(py)
        _show_setup_report(ui, report, py, findings, store)
        fixable = [f for f in findings if f.fix and f.severity in ("error", "warning")]
        options = ([("f", "Fix what is suggested")] if fixable else []) + [
            ("1", "libjxl (cjxl, djxl, jxlinfo)"), ("2", "ExifTool"), ("3", "ImageMagick"),
            ("p", "Python packages"), ("z", "Undo the last PATH change"),
            ("r", "Check again"), ("0", "Done")]
        choice = ui.choose("setup.main", "Setup", options, default="0")
        if choice == "0":
            return 0
        if choice == "f":
            _setup_fix_all(ui, store, args, report, fixable, probe, opener, py, pip_run)
        elif choice in ("1", "2", "3"):
            _setup_unit_menu(ui, store, report, ("libjxl", "exiftool", "magick")[int(choice) - 1],
                             args, probe, opener)
        elif choice == "p":
            _setup_packages(ui, py, pip_run)
        elif choice == "z":
            _undo_last_change(store, ui)


def _setup_default_args() -> argparse.Namespace:
    return argparse.Namespace(check=False, tools_dir=None, apply_system_path=None, sha256=None)


def _setup_main(argv) -> int:
    """`python jxl_photo.py setup [--check] [--tools-dir DIR]`."""
    if getattr(sys, "frozen", False):
        print("jxl-photo.exe carries its own tools; setup is for the Python version.")
        return 2
    parser = argparse.ArgumentParser(
        prog="jxl_photo.py setup", allow_abbrev=False,
        description="Check the tools the scripts find on PATH (libjxl, ExifTool, ImageMagick) "
                    "and the Python packages; fix what is wrong, switch a tool's version, or "
                    "download the tested ones.")
    parser.add_argument("--check", action="store_true",
                        help="Report only: ask nothing, write nothing. Exit 1 on an ERROR.")
    parser.add_argument("--tools-dir", metavar="DIR",
                        help="Folder for the tested downloads (default: "
                             "%%LOCALAPPDATA%%\\jxl-photo\\tools)")
    parser.add_argument("--apply-system-path", metavar="FILE",
                        help="Internal: used by the setup's own elevated step")
    parser.add_argument("--sha256", metavar="HEX",
                        help="Internal: used by the setup's own elevated step")
    args = parser.parse_args(argv)
    why = _common._long_temp_problem()
    if why:
        print(f"ERROR: {why}")
        return 2
    _common._utf8_console()
    ui = RichUI() if (RICH_AVAILABLE and console) else PlainUI()
    try:
        store = _path_store()
        if args.apply_system_path:
            return _apply_system_path_plan(store, Path(args.apply_system_path), args.sha256)
        if args.check:
            return _setup_check(store)
        return _setup_session(ui, store, args)
    except KeyboardInterrupt:
        print("\nCancelled (Ctrl+C).")
        return 130
    except EOFError:
        print("\nCancelled (end of input).")
        return 130
    except (OSError, ValueError) as e:
        print(f"ERROR: the PATH values cannot be read ({e}).")
        return 1


# === END OF SETUP ============================================================


def main():
    try:
        _main()
    except KeyboardInterrupt:
        # Ctrl+C anywhere — at a menu prompt, or in a child (which ended by
        # itself or was stopped by _stream_child, and a manifest run has
        # already summarized). Say so
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
    parser = argparse.ArgumentParser(
        description="JXL Tools v2 - JPEG XL Processing with Auto Mode",
        epilog="One script directly: jxl_photo.py encode|decode|recompress|transcode ARGS "
               "runs jxl_tiff_encoder.py, jxl_tiff_decoder.py, jxl_recompressor.py or "
               "jxl_jpeg_transcoder.py with ARGS ('jxl_photo.py encode --help' lists "
               "its options).")
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
    # A TMP of 261 characters or more: every program the wizard would start
    # (the dependency check, each backend) hangs (#689).
    _why = _common._long_temp_problem()
    if _why:
        parser.error(_why)

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
        print(f"Run '{Path(sys.executable).stem} jxl_photo.py setup' to find or install them.")
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
    # Asked in the background: the menu waits half a second at most; until
    # the answer is in it shows the one an earlier run kept (with its date).
    # The thread keeps a late answer itself, so the next menu or the next run
    # shows it.
    menu._update_poll = _update_check_start(config)
    _upd = menu._update_poll(0.5)
    if _upd:
        print(_upd[0])
        menu._update_available = (config.config.update_latest_seen, _upd[1])

    _tip = _jxl_thumbnail_tip() if config.config.show_thumbnail_tip else None
    if _tip:
        print(_tip)
        print("     (Hide this tip: 9 - Settings, then 1.)")
        menu._thumb_tip = True
    # jxl-thumb installed but older than its latest release (the same daily
    # ask; no extra network call when it was already asked today).
    _thumb_upd = _thumb_update_notice(config)
    if _thumb_upd:
        print(_thumb_upd)
        menu._thumb_tip = True
        menu._thumb_update = True

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

        elif choice == "T" and getattr(menu, '_thumb_tip', False):
            _thumb_url = (f"{JXL_THUMB_URL}/releases/latest"
                          if getattr(menu, '_thumb_update', False) else JXL_THUMB_URL)
            try:
                import webbrowser
                webbrowser.open(_thumb_url)
            except Exception:
                pass
            print(f"jxl-thumb: {_thumb_url}")
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
                                 [("1", "Wrapper defaults (workers, staging, marker, distance…)"),
                                  ("2", "Script settings (the four scripts' own defaults)"),
                                  ("3", "Reset all settings"),
                                  ("4", "Move the settings file"), ("0", "Back")], "0")
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
            # ...and the releases, now (the daily background ask may be a
            # day old, or answered by an earlier run).
            _line, _notice = _update_check_now(config)
            menu.ui.markup(f"[yellow]{_rich_safe(_line)}[/yellow]" if _notice
                           else _rich_safe(_line), _line)
            menu._update_poll = None          # D's answer stands
            menu._update_available = ((config.config.update_latest_seen, _notice[1])
                                      if _notice else None)
            _thumb_upd = _thumb_update_notice(config)
            if _thumb_upd:
                print(_thumb_upd)
                menu._thumb_tip = True
                menu._thumb_update = True
            if menu.ui.ask_yes(
                    "deps.setup",
                    "Fix or change the tools on PATH and the Python packages (setup)?",
                    "\nFix or change the tools on PATH and the Python packages (setup)? [y/N]: ",
                    False):
                _setup_session(menu.ui, _path_store(), _setup_default_args())
                status = checker.check_dependencies(force=True)
                menu.display_status(status)

        elif choice == "_reset":
            confirm = menu.ui.ask_yes(
                "settings.reset", "[red]This will erase all saved settings. Continue?[/red]",
                "\nErase all settings? [y/N]: ", False)

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

            confirm = menu.ui.ask_yes(
                "settings.move", f"[yellow]Move settings to {action}?[/yellow]",
                f"\nMove settings to {action}? [Y/n]: ", True)

            if confirm:
                try:
                    # shutil.move falls back to copy2 when the target exists,
                    # silently overwriting an existing config there — confirm first.
                    if target.exists():
                        ow = menu.ui.ask_yes(
                            "settings.move_overwrite",
                            f"[red]A settings file already exists at {_rich_safe(target.parent)}. Overwrite it?[/red]",
                            f"A settings file already exists at {target.parent}. Overwrite? [y/N]: ",
                            False)
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
    if len(sys.argv) > 1 and sys.argv[1].lower() == "setup":
        sys.exit(_setup_main(sys.argv[2:]))
    main()      # a backend command never gets here: see _run_backend's call
