"""v2.23.0: --export-jpeg-folder names the folder a JXL -> JPEG/PNG run of
modes 6/7 writes under the export marker (it was always EXPORT_JPEG_FOLDER,
'JPEG_recovered' — the bit-exact recovery's folder — with no flag).

Real cjxl/djxl/ImageMagick in a folder with Japanese characters, [ ], a space
and % (AGENTS.md); skipped when a tool is missing.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
TRANSCODER = SCRIPTS / "jxl_jpeg_transcoder.py"
HOSTILE = "250118_吾妻山 [FINAL] 5% (2)"

needs_tools = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("cjxl", "djxl", "magick")),
    reason="cjxl, djxl and ImageMagick are needed")


def _master(tmp_path):
    """A small 16-bit JXL below <event>/_EXPORT/16B_JXL."""
    png = tmp_path / "src.png"
    subprocess.run(["magick", "-size", "96x64", "gradient:red-blue", "-depth", "16",
                    str(png)], check=True, capture_output=True)
    jxl = tmp_path / "m.jxl"
    subprocess.run(["cjxl", str(png), str(jxl), "-d", "0.5"], check=True,
                   capture_output=True)
    root = tmp_path / "lib"
    folder = root / HOSTILE / "_EXPORT" / "16B_JXL"
    folder.mkdir(parents=True)
    shutil.copy2(jxl, folder / "DSC_0001 (2).jxl")
    return root


def _run(root, *extra):
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               JXLPHOTO_LOG_DIR=str(root.parent / "logs"))
    return subprocess.run(
        [sys.executable, str(TRANSCODER), str(root), "--mode", "7",
         "--export-subfolder", "16B_JXL", "--decode", "--force-convert",
         "--quality", "70", *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


@needs_tools
def test_the_jpeg_lands_in_the_named_folder(tmp_path):
    root = _master(tmp_path)
    r = _run(root, "--export-jpeg-folder", "JPG mobile [x] 5%")
    assert r.returncode == 0, r.stdout + r.stderr
    export = root / HOSTILE / "_EXPORT"
    assert (export / "JPG mobile [x] 5%" / "DSC_0001 (2).jpg").is_file()
    assert not (export / "JPEG_recovered").exists()


@needs_tools
@pytest.mark.parametrize("name", ["_EXPORT_web", "16B_JXL", "a/b"])
def test_a_bad_folder_name_is_refused(tmp_path, name):
    root = _master(tmp_path)
    r = _run(root, "--export-jpeg-folder", name)
    assert r.returncode == 2
    assert "--export-jpeg-folder" in r.stderr
    assert not list((root / HOSTILE / "_EXPORT").glob("*/*.jpg"))
