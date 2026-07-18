#!/usr/bin/env python3
"""
Version-gating tests for libjxl v0.12 support.

Covers:
- _tool_version() parsing for real-world cjxl/djxl outputs (0.11.x and 0.12.x)
- safe fallback when the binary is missing or output is garbage
- --buffering gating on pixel-encode paths (encoder + transcoder)
- djxl --reconstruct_jpeg gating on the lossless recovery path
- regression: JXL without jbrd in force-transcode decode stays a per-file
  error (does not abort the batch)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_encoder as enc
import jxl_jpeg_transcoder as tr


V0120_STDOUT = "cjxl v0.12.0 4128790 [_AVX2_,SSE4,SSE2] {Clang 22.1.3}\nCopyright (c) the JPEG XL Project\n"
V0112_STDERR = "JPEG XL encoder v0.11.2 332feb1 [AVX2,SSE2]\nUsage: cjxl INPUT OUTPUT [OPTIONS...]\n"


class _FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture(autouse=True)
def _clear_version_cache():
    enc._tool_version.cache_clear()
    tr._tool_version.cache_clear()
    yield
    enc._tool_version.cache_clear()
    tr._tool_version.cache_clear()


# ---------------------------------------------------------------------------
# _tool_version / _tool_at_least parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", [enc, tr])
def test_version_parse_v0120(monkeypatch, mod):
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _FakeRun(stdout=V0120_STDOUT))
    assert mod._tool_version("cjxl") == (0, 12, 0)
    assert mod._tool_at_least("cjxl", 0, 12) is True
    assert mod._tool_at_least("cjxl", 0, 13) is False


@pytest.mark.parametrize("mod", [enc, tr])
def test_version_parse_v0112_stderr(monkeypatch, mod):
    # 0.11.x prints the banner to stderr on -h; --version prints to stdout.
    # Either way the helper must find the version.
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _FakeRun(stderr=V0112_STDERR))
    assert mod._tool_version("djxl") == (0, 11, 2)
    assert mod._tool_at_least("djxl", 0, 12) is False


@pytest.mark.parametrize("mod", [enc, tr])
def test_version_missing_binary(monkeypatch, mod):
    def boom(*a, **k):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(mod.subprocess, "run", boom)
    assert mod._tool_version("cjxl") is None
    assert mod._tool_at_least("cjxl", 0, 12) is False


@pytest.mark.parametrize("mod", [enc, tr])
def test_version_garbage_output(monkeypatch, mod):
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _FakeRun(stdout="not a version"))
    assert mod._tool_version("cjxl") is None
    assert mod._tool_at_least("cjxl", 0, 12) is False


# ---------------------------------------------------------------------------
# --buffering gating
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", [enc, tr])
def test_buffering_flag_gating(monkeypatch, mod):
    monkeypatch.setattr(mod, "_tool_version", lambda exe: (0, 12, 0))
    monkeypatch.setattr(mod, "CJXL_BUFFERING", 0)
    assert mod._cjxl_buffering_flag() == ["--buffering=0"]

    monkeypatch.setattr(mod, "CJXL_BUFFERING", 2)
    assert mod._cjxl_buffering_flag() == ["--buffering=2"]

    # old binary: never append the flag
    monkeypatch.setattr(mod, "_tool_version", lambda exe: (0, 11, 2))
    assert mod._cjxl_buffering_flag() == []

    # unknown version: safe fallback, no flag
    monkeypatch.setattr(mod, "_tool_version", lambda exe: None)
    assert mod._cjxl_buffering_flag() == []

    # setting disabled: no flag even on new binary
    monkeypatch.setattr(mod, "_tool_version", lambda exe: (0, 12, 0))
    monkeypatch.setattr(mod, "CJXL_BUFFERING", None)
    assert mod._cjxl_buffering_flag() == []


def test_encode_to_jxl_cmd_includes_buffering(monkeypatch, tmp_path):
    """Lossy convert path: --buffering appears only when cjxl >= 0.12."""
    tr.setup_logger()
    src = tmp_path / "a.jpg"
    src.write_bytes(b"\xff\xd8fake")  # content irrelevant: cjxl is mocked
    write = tmp_path / "out" / "a.jxl"
    final = tmp_path / "out" / "a.jxl"

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _FakeRun()

    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    monkeypatch.setattr(tr, "reorder_jxl_boxes", lambda p: None)
    monkeypatch.setattr(tr, "_copy_metadata", lambda s, d: None)
    monkeypatch.setattr(tr, "FORCE_CONTAINER_FOR_LOSSY", True)
    monkeypatch.setattr(tr, "CJXL_BUFFERING", 0)

    monkeypatch.setattr(tr, "_tool_version", lambda exe: (0, 12, 0))
    res = tr.encode_to_jxl(src, write, final, effort=7, distance=1.0,
                           reconvert_val=True, smart=False)
    assert res[1] == "ok"
    assert any(c.startswith("--buffering=") for c in calls[0])

    calls.clear()
    monkeypatch.setattr(tr, "_tool_version", lambda exe: (0, 11, 2))
    res = tr.encode_to_jxl(src, write, final, effort=7, distance=1.0,
                           reconvert_val=True, smart=False)
    assert res[1] in ("ok", "reconvert")
    assert all(not c.startswith("--buffering=") for c in calls[0])


# ---------------------------------------------------------------------------
# djxl --reconstruct_jpeg gating (lossless recovery path)
# ---------------------------------------------------------------------------

def _run_decode(monkeypatch, tmp_path, version, name="a"):
    tr.setup_logger()
    jxl = tmp_path / f"{name}.jxl"
    jxl.write_bytes(b"\x00" * 16)  # has_jbrd_box is mocked below
    write = tmp_path / "out" / f"{name}.jpg"
    final = tmp_path / "out" / f"{name}.jpg"

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _FakeRun()

    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    monkeypatch.setattr(tr, "_tool_version", lambda exe: version)
    res = tr.decode_one_transcode(jxl, write, final, verify=False,
                                  reconvert_val=True, smart=False)
    return res, calls


def test_reconstruct_jpeg_added_on_v012(monkeypatch, tmp_path):
    res, calls = _run_decode(monkeypatch, tmp_path, (0, 12, 0))
    assert res[1] == "ok"
    assert calls[0][1] == "--reconstruct_jpeg"


def test_reconstruct_jpeg_absent_on_v011(monkeypatch, tmp_path):
    res, calls = _run_decode(monkeypatch, tmp_path, (0, 11, 2))
    assert res[1] == "ok"
    assert "--reconstruct_jpeg" not in calls[0]


def test_reconstruct_jpeg_absent_on_unknown_version(monkeypatch, tmp_path):
    res, calls = _run_decode(monkeypatch, tmp_path, None)
    assert res[1] == "ok"
    assert "--reconstruct_jpeg" not in calls[0]


# ---------------------------------------------------------------------------
# Regression: JXL without jbrd in force-transcode decode = per-file error
# ---------------------------------------------------------------------------

def test_missing_jbrd_is_per_file_error(monkeypatch, tmp_path):
    tr.setup_logger()
    jxl = tmp_path / "nojbrd.jxl"
    jxl.write_bytes(b"\xff\x0a" + b"\x00" * 64)  # bare codestream, no jbrd
    write = tmp_path / "out" / "nojbrd.jpg"
    final = tmp_path / "out" / "nojbrd.jpg"
    # real has_jbrd_box parses the fake file and finds no jbrd
    res = tr.decode_one_transcode(jxl, write, final, verify=False,
                                  reconvert_val=True, smart=False)
    assert res[1] == "error"
    assert "jbrd" in res[2]
