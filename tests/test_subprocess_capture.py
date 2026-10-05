#!/usr/bin/env python3
"""Round 45 — every subprocess call goes through `_run_captured`, and a real
run never starts a reader thread (N1-N5).

`capture_output=True` makes `subprocess` start two `_readerthread`s per call on
Windows; under memory exhaustion a thread's bootstrap can fail with MemoryError
before it signals "started", and `Thread.start()` then waits forever — before
the timeout is armed (the 2026-10-04 hang). `_run_captured` writes stdout/stderr
to temp FILES instead, and the version probes (`_tool_version`,
`_warn_if_libjxl_too_old`) are the only deliberate exception: they run once per
executable and swallow any failure.

N2 is a real-codec test (cjxl/djxl/exiftool/magick) and is skipped off Windows,
where `subprocess` uses no reader threads.
"""

import ast
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_recompressor as rec
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent

_SCRIPTS = (
    "jxl_tiff_encoder.py",
    "jxl_tiff_decoder.py",
    "jxl_jpeg_transcoder.py",
    "jxl_recompressor.py",
)
_SUBPROCESS_ATTRS = {"run", "Popen", "call", "check_call", "check_output"}
# The only module-level functions allowed to call subprocess directly.
_ALLOWED_OWNERS = {"_run_captured", "_tool_version", "_warn_if_libjxl_too_old"}
# The scripts' own filenames, for attributing a stack frame to a child script.
_SCRIPT_FILES = set(_SCRIPTS)
_PROBE_FUNCS = {"_tool_version", "_warn_if_libjxl_too_old"}


def _subprocess_call_owners(node, current):
    """Yield (module-level function name, lineno) for every `subprocess.<x>()`."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _subprocess_call_owners(child, child.name)
            continue
        if isinstance(child, ast.Call):
            f = child.func
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id == "subprocess"
                    and f.attr in _SUBPROCESS_ATTRS):
                yield current or "<module>", child.lineno
        yield from _subprocess_call_owners(child, current)


# ---------------------------------------------------------------------------
# N1 — no subprocess call outside the capture helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", _SCRIPTS)
def test_no_subprocess_call_outside_the_capture_helper(script):
    tree = ast.parse((REPO / script).read_text(encoding="utf-8"))
    offenders = [(owner, lineno)
                 for owner, lineno in _subprocess_call_owners(tree, None)
                 if owner not in _ALLOWED_OWNERS]
    assert not offenders, (
        f"{script}: subprocess call(s) outside _run_captured "
        "(and the two version probes):\n"
        + "\n".join(f"  line {lineno}: {owner}()" for owner, lineno in offenders))


# ---------------------------------------------------------------------------
# N2 — a real run starts no reader thread
# ---------------------------------------------------------------------------

def _reader_thread_context():
    """(is_probe, lowest_script_function) for the current stack.

    `is_probe` is True when a version probe is on the stack (its
    `capture_output=True` is the one allowed reader thread). The lowest script
    function is the innermost frame whose file is one of the four backends.
    """
    frame = sys._getframe()
    names = []
    lowest = None
    while frame is not None:
        co = frame.f_code
        names.append(co.co_name)
        if lowest is None and Path(co.co_filename).name in _SCRIPT_FILES:
            lowest = co.co_name
        frame = frame.f_back
    return any(n in _PROBE_FUNCS for n in names), lowest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows reader threads")
def test_a_real_run_starts_no_reader_thread(tmp_path, monkeypatch):
    for tool in ("cjxl", "djxl", "exiftool", "magick"):
        if shutil.which(tool) is None:
            pytest.skip(f"needs {tool}")
    from PIL import Image

    offenders = []
    original_start = threading.Thread.start

    def guard_start(self, *args, **kwargs):
        target = getattr(self, "_target", None)
        if getattr(target, "__name__", None) == "_readerthread":
            is_probe, lowest = _reader_thread_context()
            if not is_probe:
                offenders.append(lowest or "<unknown>")
                raise AssertionError(
                    "subprocess reader thread started outside _run_captured "
                    f"(in {lowest or '<unknown>'})")
        return original_start(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", guard_start)

    def run_main(mod, argv):
        old = sys.argv
        sys.argv = [mod.__name__] + [str(a) for a in argv]
        try:
            try:
                mod.main()
            except SystemExit as e:
                assert e.code in (0, None), f"{mod.__name__} exited {e.code}"
        finally:
            sys.argv = old

    # 1. Encoder: the --ram (stdin pipe) and --no-ram (file) cjxl paths.
    enc_dir = tmp_path / "enc"
    enc_dir.mkdir()
    img16 = np.zeros((48, 64, 3), dtype=np.uint16)
    img16[..., 0] = np.arange(64, dtype=np.uint16) * 1000
    img16[..., 1] = np.arange(48, dtype=np.uint16)[:, None] * 1000
    img16[..., 2] = 30000
    tifffile.imwrite(enc_dir / "a.tif", img16, photometric="rgb")
    run_main(enc, [enc_dir, "--mode", "1", "--distance", "1", "--effort", "1",
                   "--workers", "2", "--no-preflight", "--ram"])
    run_main(enc, [enc_dir, "--mode", "1", "--distance", "1", "--effort", "1",
                   "--workers", "2", "--no-preflight", "--no-ram", "--overwrite"])
    jxl_dir = enc_dir / enc.CONVERTED_JXL_FOLDER
    assert list(jxl_dir.glob("*.jxl")), "encoder produced no JXL"

    # 2. Decoder: djxl -> TIFF.
    run_main(dec, [jxl_dir, "--mode", "1"])
    assert list((jxl_dir / dec.CONVERTED_TIFF_FOLDER).glob("*.tif")), \
        "decoder produced no TIFF"

    # 3. Recompressor: a plain re-encode and the derivative path (djxl -> magick
    #    -> cjxl) into its own destination.
    rec_common = ["--on-unknown", "convert", "--on-regeneration", "convert",
                  "--on-downgrade", "convert", "--no-preflight"]
    run_main(rec, [jxl_dir, "--mode", "1", "--distance", "2", "--effort", "3"]
             + rec_common)
    assert list((jxl_dir / rec.CONVERTED_JXL_FOLDER).glob("*.jxl")), \
        "recompressor produced no JXL"
    rec_small = tmp_path / "rec_small"
    run_main(rec, [jxl_dir, rec_small, "--mode", "2", "--distance", "2",
                   "--effort", "3", "--resize-long", "32"] + rec_common)
    assert list(rec_small.glob("*.jxl")), "recompressor derivative produced no JXL"

    # 4. Transcoder: JPEG -> JXL lossless (encode path), then a PNG -> JXL
    #    convert decoded with resize — the magick derivative inside
    #    decode_to_image. A JPEG-reconstruction (jbrd) JXL refuses resize by
    #    design, so the derivative uses a PNG source and no --decode (auto
    #    infers the decode direction from the JXL input).
    jpg_dir = tmp_path / "jpg"
    jpg_dir.mkdir()
    Image.fromarray((img16 >> 8).astype(np.uint8)).save(
        jpg_dir / "a.jpg", quality=95)
    run_main(tr, [jpg_dir, "--mode", "1"])
    tr_jxl_dir = jpg_dir / tr.CONVERTED_JXL_FOLDER
    assert list(tr_jxl_dir.glob("*.jxl")), "transcoder produced no JXL"

    png_dir = tmp_path / "png"
    png_dir.mkdir()
    Image.fromarray((img16 >> 8).astype(np.uint8)).save(png_dir / "a.png")
    run_main(tr, [png_dir, "--mode", "1"])
    png_jxl_dir = png_dir / tr.CONVERTED_JXL_FOLDER
    assert list(png_jxl_dir.glob("*.jxl")), "transcoder PNG->JXL produced no JXL"
    run_main(tr, [png_jxl_dir, "--mode", "1", "--format", "jpeg",
                  "--resize-long", "32"])
    assert list((png_jxl_dir / tr.RECOVERED_JPEG_FOLDER).glob("*.jpg")), \
        "transcoder derivative produced no JPEG"

    assert not offenders, offenders


# ---------------------------------------------------------------------------
# N3 — input= is written from this thread (no reader thread for stdin)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows reader threads")
@pytest.mark.parametrize("mod", [enc, dec, tr, rec],
                         ids=["encoder", "decoder", "transcoder", "recompressor"])
def test_run_captured_feeds_input_without_a_thread(mod, monkeypatch):
    def _no_thread(*_a, **_k):
        raise AssertionError("a thread was started")

    monkeypatch.setattr(threading.Thread, "start", _no_thread)
    cmd = [sys.executable, "-c",
           "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()[::-1])"]
    r = mod._run_captured(cmd, 30, input=b"abc")
    assert r.returncode == 0
    assert r.stdout == b"cba"


# ---------------------------------------------------------------------------
# N4 — _stderr_tail keeps the END, folds CRLF, tolerates bytes/str/None
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", [enc, dec, tr, rec],
                         ids=["encoder", "decoder", "transcoder", "recompressor"])
def test_stderr_tail_keeps_the_end(mod):
    payload = b"x" * 300 + b"\r\nthe real error\r\n"
    tail = mod._stderr_tail(payload)
    assert tail.endswith("the real error"), tail
    assert "\r" not in tail
    assert len(tail) <= 200

    tail_str = mod._stderr_tail(payload.decode())
    assert tail_str.endswith("the real error")
    assert "\r" not in tail_str

    assert mod._stderr_tail(None) == ""


# ---------------------------------------------------------------------------
# N5 — decode_to_image names the tool that failed and keeps its message
# ---------------------------------------------------------------------------

def test_decode_to_image_reports_the_failing_tool(tmp_path, monkeypatch):
    jxl = tmp_path / "a.jxl"
    jxl.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)
    out = tmp_path / "a.jpg"
    tr.setup_logger()
    monkeypatch.setattr(tr, "MAGICK_AVAILABLE", True)
    monkeypatch.setattr(tr, "should_process", lambda *a, **k: True)

    def fake_run_captured(cmd, timeout, text=False, input=None):
        return subprocess.CompletedProcess(
            cmd, 1, b"", b"B" * 250 + b"\r\nreal djxl error\r\n")

    monkeypatch.setattr(tr, "_run_captured", fake_run_captured)
    (_s, status, msg, _) = tr.decode_to_image(
        jxl, out, out, 95, "jpeg", 8, "sRGB", True, False, False)
    assert status == "error"
    assert "djxl" in msg
    assert "real djxl error" in msg
