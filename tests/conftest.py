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

import sys

import pytest

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
