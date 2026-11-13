"""A script's output redirected to a file (or read through the wizard's pipe)
is UTF-8 from the first line of main() (#661).

Redirected, Python writes in the Windows code page (cp1252 here) until
setup_logger switches stdout to UTF-8 — and argparse's --help, its errors and
the transcoder's early checks print before that. A character outside the
code page there (a setting holding a Japanese folder name, shown in --help; a
Japanese path in "input path does not exist") ended in a UnicodeEncodeError
traceback, and the em dash of every help came out as a cp1252 byte. Each case
runs with its output in a file, PYTHONIOENCODING/PYTHONUTF8 removed.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py",
           "jxl_recompressor.py")
HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"


def _run_redirected(tmp_path, script, args, settings=""):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO")}
    env.update(JXLPHOTO_SETTINGS=settings, JXLPHOTO_LOG_DIR=str(tmp_path / "logs"))
    out, err = tmp_path / "out.txt", tmp_path / "err.txt"
    with open(out, "wb") as fo, open(err, "wb") as fe:
        rc = subprocess.run([sys.executable, str(REPO / script)] + [str(a) for a in args],
                            stdout=fo, stderr=fe, stdin=subprocess.DEVNULL, env=env,
                            cwd=tmp_path, timeout=120).returncode
    return rc, out.read_bytes(), err.read_bytes()


@pytest.mark.parametrize("script", SCRIPTS)
def test_help_written_to_a_file_is_utf8(tmp_path, script):
    rc, out, err = _run_redirected(tmp_path, script, ["--help"])
    assert rc == 0, err.decode("utf-8", "replace")[-2000:]
    text = out.decode("utf-8")            # cp1252 bytes (the em dash: 0x97) fail here
    assert "—" in text and "usage:" in text


def test_help_showing_a_japanese_setting_does_not_crash(tmp_path):
    folder = "書き出し 100%off"
    settings = tmp_path / HOSTILE / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"jxl_jpeg_transcoder": {"EXPORT_JPEG_FOLDER": folder}},
                                   ensure_ascii=False), encoding="utf-8")
    rc, out, err = _run_redirected(tmp_path, "jxl_jpeg_transcoder.py", ["--help"],
                                   settings=str(settings))
    assert b"Traceback" not in err, err.decode("utf-8", "replace")[-2000:]
    assert rc == 0
    assert folder in out.decode("utf-8")


def test_help_showing_a_setting_with_percent_does_not_crash(monkeypatch):
    """#662: argparse formats every help with `%` (for %(default)s), so the
    transcoder's --export-jpeg-folder help, which shows the setting's value,
    raised TypeError on a folder named like "100%off %d" — on any console."""
    sys.path.insert(0, str(REPO))
    import jxl_jpeg_transcoder as tr
    monkeypatch.setattr(tr, "EXPORT_JPEG_FOLDER", "JPEG 100%off %d")
    assert "'JPEG 100%off %d'" in " ".join(tr.build_parser().format_help().split())


def test_an_early_error_naming_a_japanese_path_does_not_crash(tmp_path):
    missing = tmp_path / HOSTILE / "nada [x].jpg"
    rc, out, err = _run_redirected(tmp_path, "jxl_jpeg_transcoder.py", [missing])
    assert b"Traceback" not in err, err.decode("utf-8", "replace")[-2000:]
    assert rc == 1
    assert f"input path does not exist: {missing}" in out.decode("utf-8")


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_command_line_error_naming_a_japanese_path_is_utf8(tmp_path, script):
    rc, out, err = _run_redirected(tmp_path, script,
                                   [tmp_path / HOSTILE, "--no-such-flag-吾妻山"])
    assert rc == 2
    assert "--no-such-flag-吾妻山" in err.decode("utf-8")
