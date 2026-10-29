#!/usr/bin/env python3
"""v2.15.0 (#537): a planning-time exiftool BATCH is stopped only when it stops
printing.

Each batch read (up to 400 paths per exiftool call) used a fixed wall-clock
limit of 180 s (120 s in the decoder's multipage reader). On a busy disk every
batch of a 2025 library timed out. The batch sites now pass
`stall=EXIFTOOL_BATCH_STALL` with `EXIFTOOL_BATCH_TIMEOUT` as the overall cap:
a child whose stdout has not grown for STALL seconds is killed, a child that is
still printing is left to finish until the cap.

W1-W4 exercise `_run_captured(..., stall=)` with small Python children (no
codec needed); W5 checks every batch call site; W6 runs real exiftool through
the stall path; W7 checks the settings.

Pre-fix proof (must fail on the 4921caf scripts):

    mkdir <tmp>\\stallpre
    git show 4921caf:jxl_tiff_encoder.py > <tmp>\\stallpre\\jxl_tiff_encoder.py
    (same for jxl_tiff_decoder, jxl_jpeg_transcoder, jxl_recompressor)
    $env:JXL_SCRIPTS_UNDER_TEST = "<tmp>\\stallpre"
    python -m pytest tests/test_batch_stall.py -q
"""

import ast
import importlib
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
sys.path.insert(0, str(Path(_OVERRIDE).resolve()) if _OVERRIDE else str(_REPO))

SCRIPTS = ["jxl_tiff_encoder", "jxl_tiff_decoder", "jxl_jpeg_transcoder",
           "jxl_recompressor"]
# Batch readers that are not named *_batch (the recompressor's mpg reader).
EXTRA_BATCH_FUNCS = {"_read_mpg_markers"}
# Batch call sites per script (base commit 4921caf: 11 in all).
# Encoder: + _read_page_markers_batch (v2.18.0, the leftover split pages, #551).
# Transcoder: + _read_thumb_markers_batch (v2.18.0, no derivative of a thumbnail
# page, #555).
EXPECTED_SITES = {"jxl_tiff_encoder": 3, "jxl_tiff_decoder": 3,
                  "jxl_jpeg_transcoder": 3, "jxl_recompressor": 4}

_REAL = all(shutil.which(t) for t in ("cjxl", "exiftool"))
real = pytest.mark.skipif(not _REAL, reason="needs cjxl and exiftool on PATH")


@pytest.fixture(params=SCRIPTS)
def mod(request):
    return importlib.import_module(request.param)


def _child(code):
    return [sys.executable, "-c", code]


# ---------------------------------------------------------------------------
# W1 — a slow child that keeps printing is NOT killed
# ---------------------------------------------------------------------------

def test_w1_slow_but_printing_child_is_not_killed(mod):
    cmd = _child("import time\n"
                 "for i in range(6):\n"
                 "    print(i, flush=True)\n"
                 "    time.sleep(1.0)\n")
    r = mod._run_captured(cmd, 60, text=True, stall=3)
    assert r.returncode == 0
    assert r.stdout.split() == ["0", "1", "2", "3", "4", "5"]
    # The old call style (a fixed 3 s limit for the same 6 s child) is the
    # 13/10 failure in miniature.
    with pytest.raises(subprocess.TimeoutExpired):
        mod._run_captured(cmd, 3, text=True)


# ---------------------------------------------------------------------------
# W2 — a silent child is killed after `stall` seconds
# ---------------------------------------------------------------------------

def test_w2_silent_child_is_killed_after_stall(mod):
    cmd = _child("import time\n"
                 "print('started', flush=True)\n"
                 "time.sleep(60)\n")
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        mod._run_captured(cmd, 60, text=True, stall=2)
    assert time.monotonic() - t0 < 10
    assert exc.value.timeout == 2


# ---------------------------------------------------------------------------
# W3 — the overall cap still fires for a child that never stops printing
# ---------------------------------------------------------------------------

def test_w3_overall_cap_fires_for_a_chatty_child(mod):
    cmd = _child("import time\n"
                 "while True:\n"
                 "    print('x', flush=True)\n"
                 "    time.sleep(0.3)\n")
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        mod._run_captured(cmd, 3, text=True, stall=10)
    assert time.monotonic() - t0 < 10
    assert exc.value.timeout == 3


# ---------------------------------------------------------------------------
# W4 — the stall path starts no reader thread (Windows, like W-thread in
# test_memory_workers.py)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows reader threads")
def test_w4_stall_path_starts_no_thread(mod, monkeypatch):
    def _boom(self, *a, **k):
        raise AssertionError("_run_captured started a thread")

    monkeypatch.setattr(threading.Thread, "start", _boom)
    cmd = _child("print('ok', flush=True)")
    r = mod._run_captured(cmd, 30, text=True, stall=5)
    assert r.returncode == 0 and r.stdout.strip() == "ok"


# ---------------------------------------------------------------------------
# W5 — every batch call site reads the settings (AST)
# ---------------------------------------------------------------------------

def _batch_exiftool_calls(script):
    # The module under test (the override directory when one is set)
    path = Path(importlib.import_module(script).__file__)
    src = path.read_text(encoding="utf-8")
    rows = []

    class V(ast.NodeVisitor):
        stack = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            fn = self.stack[-1] if self.stack else ""
            is_batch = fn.endswith("_batch") or fn in EXTRA_BATCH_FUNCS
            if (getattr(node.func, "id", None) == "_run_captured" and is_batch
                    and '"-@"' in (ast.get_source_segment(src, node) or "")):
                rows.append((fn, node))
            self.generic_visit(node)

    V().visit(ast.parse(src))
    return rows


@pytest.mark.parametrize("script", SCRIPTS)
def test_w5_every_batch_site_uses_the_settings(script):
    calls = _batch_exiftool_calls(script)
    assert len(calls) == EXPECTED_SITES[script], [fn for fn, _ in calls]
    for fn, node in calls:
        timeout = node.args[1] if len(node.args) > 1 else next(
            (kw.value for kw in node.keywords if kw.arg == "timeout"), None)
        assert isinstance(timeout, ast.Name) and \
            timeout.id == "EXIFTOOL_BATCH_TIMEOUT", (script, fn, ast.unparse(timeout))
        stall = next((kw.value for kw in node.keywords if kw.arg == "stall"), None)
        assert isinstance(stall, ast.Name) and \
            stall.id == "EXIFTOOL_BATCH_STALL", (script, fn, "no stall=EXIFTOOL_BATCH_STALL")


# ---------------------------------------------------------------------------
# W6 — real exiftool through the stall path (the parser still reads the markers)
# ---------------------------------------------------------------------------

@real
@pytest.mark.parametrize("script", SCRIPTS)
def test_w6_real_exiftool_batch_read_through_the_stall_path(script, tmp_path):
    from PIL import Image
    mod = importlib.import_module(script)
    paths = []
    for i in range(3):
        png = tmp_path / f"p{i}.png"
        Image.new("RGB", (32, 32), (i * 40, 90, 200)).save(png)
        jxl = tmp_path / f"p{i}.jxl"
        # --container=1: exiftool cannot write XMP into a bare codestream
        r = subprocess.run(["cjxl", str(png), str(jxl), "-d", "1", "-e", "3",
                            "--container=1"],
                           capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        paths.append(jxl)
    subprocess.run(
        ["exiftool", "-overwrite_original",
         "-XMP-dc:Relation+=jxlphoto-src:0123456789abcdef",
         "-XMP-dc:Relation+=jxlphoto-srcsum:fedcba9876543210"] + [str(p) for p in paths],
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, check=True)

    got = mod._read_source_markers_batch(paths)
    for p in paths:
        assert got[str(p)] == {"src": "0123456789abcdef",
                               "srcsum": "fedcba9876543210"}, got


# ---------------------------------------------------------------------------
# W8 — an error inside the polling loop never leaves the child running
# (subprocess.run kills its child on any exception; the stall path must too)
# ---------------------------------------------------------------------------

def test_w8_an_error_while_polling_kills_the_child(mod, monkeypatch):
    started = []
    real_popen = subprocess.Popen

    class _Recording(real_popen):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            started.append(self)

    class _OsProxy:
        def __getattr__(self, name):
            return getattr(os, name)

        def fstat(self, fd):
            raise OSError("simulated fstat failure")

    monkeypatch.setattr(subprocess, "Popen", _Recording)
    monkeypatch.setattr(mod, "os", _OsProxy())
    cmd = _child("import time\ntime.sleep(60)\n")
    with pytest.raises(OSError, match="simulated"):
        mod._run_captured(cmd, 60, text=True, stall=30)
    assert len(started) == 1
    assert started[0].poll() is not None, "the child was left running"


# ---------------------------------------------------------------------------
# W7 — the settings: present in every backend, equal, and ordered
# ---------------------------------------------------------------------------

def test_w7_batch_settings_agree_across_the_backends():
    values = set()
    for name in SCRIPTS:
        mod = importlib.import_module(name)
        stall = mod.EXIFTOOL_BATCH_STALL
        timeout = mod.EXIFTOOL_BATCH_TIMEOUT
        assert 0 < stall < timeout, (name, stall, timeout)
        values.add((stall, timeout))
    assert len(values) == 1, values
