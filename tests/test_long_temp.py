"""A TMP of 261 characters or more is refused before the first program (#689).

On Windows, a process whose TMP (its TEMP when TMP is not set) is 261 UTF-16
units long or longer hangs when it starts another program — cmd, cjxl, djxl
alike, with nothing on the screen (measured 2026-11-22, build 26300: 260 runs,
261 hangs). photo-backup points TMP at its work folder and found it: a decode
waited forever on its first `djxl --version`. Every backend, a serve process
and the wizard now stop at their start with exit 2 and the reason.
"""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo_common as common  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(os.name != "nt", reason="a Windows limit")
# Japanese, brackets and a space; no '%': that one has its own refusal (#687).
HOSTILE = "吾妻山公園 [FINAL] x"


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _of_length(base: str, n: int, filler: str = "x") -> str:
    p = base
    while _u16(p) < n:
        room = n - _u16(p) - 1
        p = p + "\\" + (filler * 40)[:max(1, min(room, 40))]
    assert _u16(p) == n
    return p


@pytest.mark.parametrize("n,refused", [(260, False), (261, True), (300, True)])
def test_the_limit_is_261_utf16_units(n, refused):
    tmp = _of_length("C:\\t", n)
    why = common._long_temp_problem({"TMP": tmp, "TEMP": "C:\\short"})
    assert (why is not None) is refused
    if refused:
        assert why.startswith(f"the TMP variable is {n} characters long")
        assert "261 characters or more" in why


def test_japanese_counts_one_and_an_emoji_two():
    assert common._long_temp_problem({"TMP": _of_length("C:\\t", 260, "吾")}) is None
    assert common._long_temp_problem({"TMP": _of_length("C:\\t", 261, "吾")})
    emoji = _of_length("C:\\t\\📷", 261)          # 260 code points, 261 units
    assert len(emoji) == 260
    assert common._long_temp_problem({"TMP": emoji})


def test_a_trailing_backslash_counts():
    assert common._long_temp_problem({"TMP": _of_length("C:\\t", 260) + "\\"})


def test_temp_counts_only_without_tmp():
    long_ = _of_length("C:\\t", 261)
    assert common._long_temp_problem({"TMP": "C:\\short", "TEMP": long_}) is None
    why = common._long_temp_problem({"TEMP": long_})
    assert why and why.startswith("the TEMP variable is 261 characters long")
    # An EMPTY TMP is the one Windows takes: with TEMP at 261 or 300 a child
    # still starts (measured; the review of rc4), so it is not refused.
    assert common._long_temp_problem({"TMP": "", "TEMP": long_}) is None
    assert common._long_temp_problem({}) is None


# --- real runs: refused at the start, nothing written ----------------------

@pytest.fixture
def long_tmp(tmp_path):
    folder = _of_length(str(tmp_path / HOSTILE), 261)
    try:
        os.makedirs(folder)
    except OSError as e:
        pytest.skip(f"no long-path support here: {e}")
    return folder


def _run(args, long_tmp, tmp_path):
    env = dict(os.environ, TMP=long_tmp, TEMP=long_tmp,
               JXLPHOTO_LOG_DIR=str(tmp_path / "logs"), JXLPHOTO_SETTINGS="")
    # Before #689 the child hung on its first program: the timeout is the
    # failure (a refusal takes about a second).
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120,
                          stdin=subprocess.DEVNULL, env=env)


def _source(tmp_path):
    src = tmp_path / HOSTILE / "in"
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "DSC_0001 (2).tif"), np.full((16, 16, 3), 1200, np.uint16),
                     photometric="rgb")
    (src / "DSC_0002 (2).jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (src / "DSC_0003 (2).jxl").write_bytes(b"\xff\x0a")
    return src


@pytest.mark.parametrize("script", ["jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
                                    "jxl_jpeg_transcoder.py", "jxl_recompressor.py"])
def test_every_backend_refuses_it_before_any_program(script, long_tmp, tmp_path):
    src = _source(tmp_path)
    before = {p.name: p.read_bytes() for p in src.iterdir()}
    out = tmp_path / HOSTILE / "out"
    r = _run([REPO / script, src, out, "--mode", "0", "--workers", "1"], long_tmp, tmp_path)
    assert r.returncode == 2, r.stdout[-2000:] + r.stderr[-2000:]
    assert "the TMP variable is 261 characters long" in r.stderr
    assert "261 characters or more" in r.stderr
    assert not out.exists() or not any(out.rglob("*"))
    assert {p.name: p.read_bytes() for p in src.iterdir()} == before


def test_the_wrapper_s_subcommand_and_its_menu_refuse_it(long_tmp, tmp_path):
    src = _source(tmp_path)
    r = _run([REPO / "jxl_photo.py", "encode", src, tmp_path / HOSTILE / "out",
              "--mode", "0"], long_tmp, tmp_path)
    assert r.returncode == 2 and "261 characters or more" in r.stderr
    r = _run([REPO / "jxl_photo.py", "--list-presets"], long_tmp, tmp_path)
    assert r.returncode == 2 and "261 characters or more" in r.stderr


def test_a_serve_process_refuses_it_before_its_first_line(long_tmp, tmp_path):
    r = _run([REPO / "jxl_tiff_encoder.py", "--serve", "--mode", "0", "--workers", "1"],
             long_tmp, tmp_path)
    assert r.returncode == 2
    assert r.stdout == ""                    # no presentation line: the caller falls back
    assert r.stderr.startswith("--serve cannot start: the TMP variable is 261 characters")
