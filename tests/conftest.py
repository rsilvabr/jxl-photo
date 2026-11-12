#!/usr/bin/env python3
"""Suite-wide isolation for the child scripts' run-scoped globals.

The four child scripts mutate module-level globals from CLI flags (`if args.x:`
one-way assignments). Each script got its own reset at `main()` entry in round
43, but an in-process `main()` call in a test still leaves the process armed
until the next `main()`: `tests/test_audit_round37.py` followed by
`tests/test_audit_round40.py` produced two failures purely from a leaked
`dec.DELETE_SOURCE`. The transcoder's `_tool_version` lru_cache had the mirror
problem — a warmed cache turned the eager dry-run subprocess into a no-op, so
`tests/test_transcoder_fixes.py` only passed when another test had warmed it
first.

This fixture makes every test independent of execution order: it snapshots the
run-scoped globals around each test and restores them afterwards, and clears
the transcoder's tool-version cache before and after. It restores AFTER the
test (never resets before): a test that intentionally sets a global mid-test
must see it during the test.

The global lists come from each module's OWN mechanism — the decoder's
`_RUN_ASSIGNABLE`, the other three's `_RUN_DEFAULTS` keys — so the fixture
cannot drift from the modules. The transcoder's resize/sharpen state is reset
inline at the top of its `main()`, so those names are spelled out here.

Modules are looked up in `sys.modules` lazily, never imported here: test files
that point `JXL_ENCODER_UNDER_TEST` at an extracted copy must be the first to
import `jxl_tiff_encoder`, or the override would be silently ignored. A module
a test imports for the FIRST time has no pre-test state to restore; its
import-time values are captured instead (`_RUN_DEFAULTS` / the decoder's
pre-run record), so even that test cannot leak its flags.
"""

import logging
import os
import shutil
import sys
import tempfile
import types

import pytest

# Every log the suite produces — in-process main() runs and scripts run as
# subprocesses — goes to a throwaway folder instead of Logs\ next to the
# scripts (that folder had grown to ~25 700 files, synced by OneDrive).
_TEST_LOG_DIR = tempfile.mkdtemp(prefix="jxlphoto_test_logs_")
os.environ["JXLPHOTO_LOG_DIR"] = _TEST_LOG_DIR
# No script settings overlay: a real one on this PC (next to the scripts or
# in the home folder) must never change what the suite runs. Empty = off;
# the overlay tests set their own file.
os.environ["JXLPHOTO_SETTINGS"] = ""
# No "new release" check: a test never asks GitHub.
os.environ["JXLPHOTO_NO_UPDATE_CHECK"] = "1"

# A wide console for rich: it wraps at the terminal width, so a phrase the
# tests look for could be split by the wrap — and under pytest-xdist
# (`-n 8`, the suite's normal command) the tmp paths are longer and the
# worker has no real terminal. Set before any test module imports the
# wrapper, which builds its Console at import time.
os.environ.setdefault("COLUMNS", "240")

# The menu's Explorer-thumbnail tip depends on this PC's registry: off, so a
# test that scripts the menu or the settings screen sees the same prompts on
# every machine. tests/test_thumbnail_tip.py turns it back on per test.
os.environ["JXLPHOTO_THUMBNAIL_TIP"] = ""

# EXPLORER_THUMBNAIL = "auto" covers lossy outputs too when jxl-thumb is
# installed for the user running the tests: pinned to "not installed" so the
# encodes are the same on every machine (test_explorer_thumbnail.py sets 1
# where it means to).
os.environ["JXLPHOTO_JXL_THUMB"] = "0"


def pytest_sessionfinish(session, exitstatus):
    # The scripts' FileHandlers keep their last log open, and Windows cannot
    # delete an open file: close them first or the folder is left behind.
    logging.shutdown()
    shutil.rmtree(_TEST_LOG_DIR, ignore_errors=True)


# Reset inline at the top of the transcoder's main(), outside _RUN_DEFAULTS.
_TRANSCODER_EXTRA_GLOBALS = (
    "RESIZE_MODE", "RESIZE_VALUE", "ALLOW_UPSCALE",
    "SHARPEN", "SHARPEN_SIGMA", "SHARPEN_GAIN", "SHARPEN_THRESHOLD",
)

_CHILD_MODULES = (
    "jxl_tiff_encoder",
    "jxl_tiff_decoder",
    "jxl_jpeg_transcoder",
    "jxl_recompressor",
)


def _run_global_names(mod) -> tuple:
    names = []
    defaults = getattr(mod, "_RUN_DEFAULTS", None)
    if isinstance(defaults, dict):
        names.extend(defaults)
    assignable = getattr(mod, "_RUN_ASSIGNABLE", None)
    if assignable:
        names.extend(assignable)
    if mod.__name__ == "jxl_jpeg_transcoder":
        names.extend(_TRANSCODER_EXTRA_GLOBALS)
    return tuple(dict.fromkeys(names))


def _snapshot(mod) -> dict:
    return {name: getattr(mod, name)
            for name in _run_global_names(mod) if hasattr(mod, name)}


@pytest.fixture(autouse=True)
def _isolate_run_globals():
    transcoder = sys.modules.get("jxl_jpeg_transcoder")
    if transcoder is not None and hasattr(transcoder, "_tool_version"):
        transcoder._tool_version.cache_clear()
    if transcoder is not None and hasattr(transcoder, "_planned_outputs"):
        transcoder._planned_outputs.clear()

    saved = {name: _snapshot(sys.modules[name])
             for name in _CHILD_MODULES if name in sys.modules}
    try:
        yield
    finally:
        for name in _CHILD_MODULES:
            mod = sys.modules.get(name)
            if mod is None:
                continue
            if name in saved:
                values = saved[name]
            else:
                # First imported DURING this test: restore its import-time
                # settings (its own _RUN_DEFAULTS where it has one).
                values = dict(getattr(mod, "_RUN_DEFAULTS", None) or {})
            for gname, value in values.items():
                setattr(mod, gname, value)
        # The decoder restores its globals at the next main() entry from a delta
        # record; a stale record would replay over the values restored above.
        decoder = sys.modules.get("jxl_tiff_decoder")
        prev = getattr(decoder, "_prev_run_globals", None) if decoder is not None else None
        if isinstance(prev, dict):
            if "jxl_tiff_decoder" not in saved:
                # First imported during this test: the record holds the values
                # it had BEFORE that test's runs — exactly what to restore.
                for gname, value in prev.items():
                    setattr(decoder, gname, value)
            prev.clear()
        transcoder = sys.modules.get("jxl_jpeg_transcoder")
        if transcoder is not None and hasattr(transcoder, "_tool_version"):
            transcoder._tool_version.cache_clear()


@pytest.fixture(autouse=True)
def _no_worker_memory_cap():
    """The worker memory cap reads the REAL machine's free memory; existing
    tests must not depend on it. Tests of the cap set the fraction back.

    Restores by hand instead of using the `monkeypatch` fixture: depending on
    `monkeypatch` forces it to be set up as part of this autouse fixture, which
    moved its UNDO after the module-level `_clear_version_cache` teardown in
    tests/test_version_gating.py and made that teardown call cache_clear() on a
    monkeypatched plain function. Snapshot/restore keeps the original fixture
    ordering.
    """
    saved = []
    for name in ("jxl_tiff_encoder", "jxl_recompressor"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "WORKER_MEMORY_FRACTION"):
            saved.append((mod, mod.WORKER_MEMORY_FRACTION))
            mod.WORKER_MEMORY_FRACTION = 0
    try:
        yield
    finally:
        for mod, value in saved:
            mod.WORKER_MEMORY_FRACTION = value


# ---------------------------------------------------------------------------
# The common module (jxl_photo_common.py): a test double must reach the code
# that moved there. A script binds a pure shared helper by name
# (`X = _common.X`) and shares the standard modules with it (`os`, `time`,
# `subprocess`), so `monkeypatch.setattr(enc, "X", fake)` alone replaces the
# SCRIPT's binding: a common function reading X directly would keep the real
# one, and the test would pass without testing what it did when that code
# lived in the script. So the double is MIRRORED into the common module for
# the test, whenever the common code the script uses (the functions it binds
# or shims, and what they call) reads that name directly and the script's
# binding was the common module's own object. The functions that read the
# script through `g.` need nothing: they see the script's binding.
# tests/test_common_module.py checks the mirror; the `mock.patch.object`
# forms, which this cannot see, are checked there statically.
# ---------------------------------------------------------------------------

_COMMON_READS = {}
_MISSING = object()


def _common_reads(common) -> dict:
    """{function of the common module: the globals it reads directly (not
    via g), its nested functions included}."""
    path = getattr(common, "__file__", None)
    if not path:
        return {}
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return {}
    if key not in _COMMON_READS:
        import symtable
        with open(path, encoding="utf-8") as f:
            table = symtable.symtable(f.read(), path, "exec")
        reads = {}

        def walk(t, names):
            for s in t.get_symbols():
                if s.is_global() and s.is_referenced():
                    names.add(s.get_name())
            for c in t.get_children():
                walk(c, names)

        for child in table.get_children():
            names = set()
            walk(child, names)
            reads[child.get_name()] = frozenset(names)
        _COMMON_READS[key] = reads
    return _COMMON_READS[key]


def _names_read_for(target, common) -> set:
    """What the common code the script `target` uses reads directly: every
    common function the script binds or shims (an attribute of the same
    name), and every common function those call directly, transitively."""
    reads = _common_reads(common)
    stack = [fn for fn in reads if hasattr(target, fn)]
    seen, names = set(), set()
    while stack:
        fn = stack.pop()
        if fn in seen:
            continue
        seen.add(fn)
        names |= reads.get(fn, frozenset())
        stack.extend(n for n in reads.get(fn, ()) if n in reads)
    return names


def _mirror_target(target, name):
    """The common module to mirror `target.name` into, or None."""
    common = getattr(target, "_common", None)
    if not isinstance(common, types.ModuleType) or not isinstance(name, str):
        return None
    if getattr(target, name, _MISSING) is not getattr(common, name, _MISSING):
        return None
    return common if name in _names_read_for(target, common) else None


@pytest.fixture
def monkeypatch(monkeypatch):
    original = monkeypatch.setattr

    def setattr(*args, **kwargs):
        target = name = None
        if args and isinstance(args[0], str) and "." in args[0]:
            modpath, _, name = args[0].rpartition(".")
            target = sys.modules.get(modpath)
        elif len(args) >= 2:
            target, name = args[0], args[1]
        elif args and "name" in kwargs:
            target, name = args[0], kwargs["name"]
        common = _mirror_target(target, name) if target is not None else None
        result = original(*args, **kwargs)
        if common is not None:
            original(common, name, getattr(target, name))
        return result

    monkeypatch.setattr = setattr
    return monkeypatch
