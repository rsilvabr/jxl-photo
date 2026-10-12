#!/usr/bin/env python3
"""One toolkit version, identical in the five scripts (plan 261013, xmp-history).

TOOLKIT_VERSION is what every output records in its xmpMM:History entry, so a
release that bumped four scripts out of five would leave files honestly naming
a version that never wrote them. The scripts are read as TEXT here (regex, no
import): a child that fails to import must still be checked, and a script from
before the constant existed must fail as missing — not as an import error.
"""

import importlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = [
    "jxl_photo.py",
    "jxl_tiff_encoder.py",
    "jxl_tiff_decoder.py",
    "jxl_jpeg_transcoder.py",
    "jxl_recompressor.py",
]
BACKENDS = SCRIPTS[1:]


def _value(name: str, script: str) -> str:
    text = (REPO / script).read_text(encoding="utf-8", errors="replace")
    matches = re.findall(rf'^{name}\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert len(matches) == 1, (
        f"{script}: expected exactly one {name}, found {len(matches)}")
    return matches[0]


def test_v1_the_five_scripts_define_the_same_constants():
    names = {_value("TOOLKIT_NAME", s) for s in SCRIPTS}
    versions = {_value("TOOLKIT_VERSION", s) for s in SCRIPTS}
    urls = {_value("TOOLKIT_URL", s) for s in SCRIPTS}
    assert len(names) == 1, f"TOOLKIT_NAME differs between scripts: {names}"
    assert len(versions) == 1, f"TOOLKIT_VERSION differs between scripts: {versions}"
    assert len(urls) == 1, f"TOOLKIT_URL differs between scripts: {urls}"


def test_v2_the_version_is_a_release_version():
    version = _value("TOOLKIT_VERSION", "jxl_photo.py")
    assert re.fullmatch(r"\d+\.\d+\.\d+(_beta\d+)?", version), version


def test_v3_the_version_matches_the_newest_version_history_section():
    text = (REPO / "docs" / "version_history.md").read_text(
        encoding="utf-8", errors="replace")
    m = re.search(r"^## v(\S+)\s*$", text, re.MULTILINE)
    assert m, "docs/version_history.md has no '## vX.Y.Z' section"
    version = _value("TOOLKIT_VERSION", "jxl_photo.py")
    assert version == m.group(1), (
        "bump TOOLKIT_VERSION in all five scripts to match the newest "
        "version_history section")


def test_v4_history_mime_agrees_across_the_four_backends():
    values = set()
    for script in BACKENDS:
        mod = importlib.import_module(script[:-3])
        values.add(repr(sorted(mod._HISTORY_MIME.items())))
    assert len(values) == 1, f"_HISTORY_MIME differs between scripts: {values}"


@pytest.mark.parametrize("script", SCRIPTS)
def test_v5_version_flag_prints_the_toolkit_version(script):
    version = _value("TOOLKIT_VERSION", script)
    r = subprocess.run([sys.executable, str(REPO / script), "--version"],
                       capture_output=True, text=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, f"{script} --version failed:\n{r.stdout}{r.stderr}"
    assert r.stdout == f"jxl-photo {version}\n", repr(r.stdout)


def test_v6_script_version_reads_the_constant_as_text(tmp_path):
    good = tmp_path / "good.py"
    good.write_text('TOOLKIT_VERSION = "9.9.9"\n', encoding="utf-8")
    assert jxl_photo._script_version(good) == "9.9.9"

    missing = tmp_path / "missing.py"
    missing.write_text("# no constant here\n", encoding="utf-8")
    assert jxl_photo._script_version(missing) is None

    assert jxl_photo._script_version(tmp_path / "nope.py") is None


def _copy_children(tmp_path: Path) -> Path:
    script_dir = tmp_path / "scripts"
    script_dir.mkdir(parents=True)
    for name in jxl_photo._VERSIONED_CHILDREN:
        shutil.copy(str(REPO / name), str(script_dir / name))
    return script_dir


def _rewrite_version(path: Path, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    text, n = re.subn(r'^TOOLKIT_VERSION\s*=\s*"[^"]+"', f'TOOLKIT_VERSION = "{value}"',
                      text, flags=re.MULTILINE)
    assert n == 1, f"{path.name}: TOOLKIT_VERSION not found"
    path.write_text(text, encoding="utf-8")


def _drop_version(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text, n = re.subn(r'^TOOLKIT_VERSION\s*=\s*"[^"]+"\n', "", text,
                      flags=re.MULTILINE)
    assert n == 1, f"{path.name}: TOOLKIT_VERSION not found"
    path.write_text(text, encoding="utf-8")


def test_v7_mixed_version_messages(tmp_path):
    script_dir = _copy_children(tmp_path)
    assert jxl_photo._mixed_version_messages(script_dir) == []

    _rewrite_version(script_dir / "jxl_recompressor.py", "0.0.1")
    msgs = jxl_photo._mixed_version_messages(script_dir)
    assert len(msgs) == 1 and "jxl_recompressor.py is 0.0.1" in msgs[0], msgs

    script_dir = _copy_children(tmp_path / "b")
    _drop_version(script_dir / "jxl_tiff_decoder.py")
    msgs = jxl_photo._mixed_version_messages(script_dir)
    assert len(msgs) == 1 and "no TOOLKIT_VERSION" in msgs[0], msgs

    script_dir = _copy_children(tmp_path / "c")
    (script_dir / "jxl_jpeg_transcoder.py").unlink()
    msgs = jxl_photo._mixed_version_messages(script_dir)
    assert msgs == [], msgs
