"""`python jxl_photo.py encode|decode|recompress|transcode ARGS` runs that
backend exactly as `python <its script> ARGS` would (refactor F5.3): the same
names as jxl-photo.exe, the script next to the wrapper, run as __main__, its
exit code passed through. And the four backends refuse an abbreviated flag
(F5.2, allow_abbrev=False): a shortened `--delete-so` used to be read as
--delete-source.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_photo as wp  # noqa: E402

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"
SCRIPTS = {"encode": "jxl_tiff_encoder.py", "decode": "jxl_tiff_decoder.py",
           "recompress": "jxl_recompressor.py", "transcode": "jxl_jpeg_transcoder.py"}
NON_ASCII = re.compile("[^\u0000-\u007f]")


def _py(*args, cwd=None):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600,
                          stdin=subprocess.DEVNULL, cwd=cwd)


def test_the_names_are_the_exe_s():
    assert wp._BACKEND_COMMANDS == SCRIPTS


@pytest.mark.parametrize("name,script", sorted(SCRIPTS.items()))
def test_a_subcommand_prints_the_backend_s_own_help(name, script):
    via = _py(REPO / "jxl_photo.py", name, "--help")
    direct = _py(REPO / script, "--help")
    assert via.returncode == direct.returncode == 0
    # The same text. Only its encoding may differ: run directly, a backend
    # prints --help before it switches stdout to UTF-8 (a redirected stdout is
    # cp1252 there); through the wrapper it is UTF-8 already.
    assert NON_ASCII.sub("?", via.stdout) == NON_ASCII.sub("?", direct.stdout)


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_a_usage_error_keeps_the_backend_s_exit_code(name):
    r = _py(REPO / "jxl_photo.py", name, "in", "--no-such-flag")
    assert r.returncode == 2
    assert "unrecognized arguments: --no-such-flag" in r.stderr


def test_the_wrapper_s_help_lists_the_subcommands():
    r = _py(REPO / "jxl_photo.py", "--help")
    assert r.returncode == 0
    for name in SCRIPTS:
        assert name in r.stdout


@pytest.mark.skipif(not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
                    reason="needs cjxl, djxl and exiftool on PATH")
def test_encode_through_the_wrapper_writes_the_master(tmp_path):
    src = tmp_path / HOSTILE / "TIFF16"
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "DSC_0001 (2).tif"), np.full((16, 16, 3), 1200, np.uint16),
                     photometric="rgb")
    out = tmp_path / HOSTILE / "16B_JXL"
    r = _py(REPO / "jxl_photo.py", "encode", src, out, "--mode", "2", "--distance", "0",
            "--workers", "1")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert (out / "DSC_0001 (2).jxl").is_file()


# --- F5.2: no abbreviations -------------------------------------------------

@pytest.mark.parametrize("script", sorted(SCRIPTS.values()))
@pytest.mark.parametrize("flag", ["--delete-so", "--work", "--mod"])
def test_a_backend_refuses_an_abbreviated_flag(script, flag):
    import importlib
    parser = importlib.import_module(script[:-3]).build_parser()
    with pytest.raises(SystemExit) as e:
        parser.parse_args(["in", flag, "1"] if flag != "--delete-so" else ["in", flag])
    assert e.value.code == 2
