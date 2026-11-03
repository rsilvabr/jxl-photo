#!/usr/bin/env python3
"""T-B and W-B of the 2026-11-03 audit.

T-B (the owner's option 1): a JXL decoded to a JPEG (lossy) or to an 8-bit PNG
is not a copy of the master, so it never deletes it — like the decoder's 8-bit
TIFF of a deeper source and every derivative. The transcoder refuses the
explicit pair (`--force-convert --decode --delete-source`, exit 2) and keeps
such JXLs per file in its lossy gates (auto mode included); the wrapper
refuses the direct run before the HHMM token and drops the delete from a
manifest row. The bit-exact recovery of a JPEG archive and a 16-bit PNG still
delete.

W-B: a `--mode` in the expert flags won in the child's argparse, behind
gates that had judged the wizard's mode. The wrapper refuses it.

Pre-fix proof: JXL_SCRIPTS_UNDER_TEST=<the five scripts of the base commit>.
"""

import os
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
SCRIPTS = Path(_OVERRIDE).resolve() if _OVERRIDE else _REPO
sys.path.insert(0, str(SCRIPTS))

import jxl_jpeg_transcoder as tra  # noqa: E402
import jxl_photo as wp  # noqa: E402

HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
_HAVE_CODECS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real_codecs = pytest.mark.skipif(not _HAVE_CODECS,
                                 reason="needs cjxl, djxl and exiftool on PATH")


def _png(path: Path, depth: int):
    def chunk(n, d):
        return struct.pack(">I", len(d)) + n + d + struct.pack(">I", zlib.crc32(n + d))
    raw = b"\x00" + b"\x00" * (3 * (depth // 8))
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, depth, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


# ---------------------------------------------------------------------------
# T-B in the transcoder
# ---------------------------------------------------------------------------

def test_keep_reason_per_output(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    src = d / "DSC_0001 (2).jxl"
    src.write_bytes(b"x")
    jpg = d / "DSC_0001 (2).jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xd9")
    assert tra._lossy_decode_keep_reason(src, jpg)
    assert tra._lossy_decode_keep_reason(src, _png(d / "a8.png", 8))
    assert tra._lossy_decode_keep_reason(src, _png(d / "a16.png", 16)) is None
    # The other direction (JPEG/PNG -> JXL) is never judged here.
    assert tra._lossy_decode_keep_reason(jpg, d / "x.jxl") is None


@pytest.mark.parametrize("extra", [[], ["--format", "jpg"],
                                   ["--format", "png", "--bit-depth", "8"]])
def test_explicit_lossy_decode_with_delete_exits_2(tmp_path, extra):
    d = tmp_path / HOSTILE
    d.mkdir()
    (d / "DSC_0001 (2).jxl").write_bytes(b"x")
    r = subprocess.run([sys.executable, str(SCRIPTS / "jxl_jpeg_transcoder.py"), str(d),
                        "--force-convert", "--decode", "--delete-source",
                        "--delete-confirm-off"] + extra,
                       capture_output=True, text=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "not a copy of the JXL" in " ".join(r.stderr.split()), r.stderr


def test_16bit_png_decode_with_delete_is_not_refused(tmp_path):
    """Control: a 16-bit PNG decode may still delete its JXL."""
    d = tmp_path / HOSTILE
    d.mkdir()
    (d / "DSC_0001 (2).jxl").write_bytes(b"x")
    r = subprocess.run([sys.executable, str(SCRIPTS / "jxl_jpeg_transcoder.py"), str(d),
                        "--force-convert", "--decode", "--format", "png",
                        "--bit-depth", "16", "--delete-source", "--delete-confirm-off",
                        "--dry-run"],
                       capture_output=True, text=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    assert "not a copy of the JXL" not in r.stderr, r.stderr


@real_codecs
def test_auto_mode_keeps_a_jxl_it_converted_lossy(tmp_path):
    """--from-jxl auto mode: a JXL without jbrd is converted lossy and must be
    kept, even with --delete-source."""
    from PIL import Image
    d = tmp_path / HOSTILE
    d.mkdir()
    png = d / "src.png"
    Image.new("RGB", (64, 48), (120, 40, 200)).save(png)
    jxl = d / "DSC_0001 (2).jxl"
    subprocess.run(["cjxl", str(png), str(jxl), "-d", "1"], check=True,
                   capture_output=True)
    png.unlink()
    r = subprocess.run([sys.executable, str(SCRIPTS / "jxl_jpeg_transcoder.py"), str(d),
                        "--from-jxl", "--delete-source", "--delete-confirm-off"],
                       capture_output=True, text=True, timeout=600,
                       stdin=subprocess.DEVNULL)
    assert jxl.exists(), "the JXL was deleted behind a lossy JPEG\n" + r.stdout
    assert list(d.rglob("*.jpg")), r.stdout


# ---------------------------------------------------------------------------
# The wrapper
# ---------------------------------------------------------------------------

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    m = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    calls = {"child": [], "token": 0}
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=None:
                        (calls["child"].append(list(map(str, cmd))), 0)[1])
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode",
                        lambda self: calls.__setitem__("token", calls["token"] + 1) or True)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_in_place_replace",
                        lambda self: calls.__setitem__("token", calls["token"] + 1) or True)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, workflow: None)
    m._calls = calls
    return m


def _wf(tmp_path, dest, conv, **over):
    d = tmp_path / HOSTILE / "16B_JXL (2)"
    d.mkdir(parents=True, exist_ok=True)
    wf = {"input_dir": str(d), "origin_format": "jxl", "dest_format": dest,
          "conversion_type": conv, "mode": 1, "workers": 2, "staging": None,
          "icc_profile": None, "use_ram": True, "compression": None,
          "bit_depth": None, "add_preview": True, "dry_run": False,
          "advanced_options": {"delete_source": True}, "expert_flags": "",
          "mode_config": {}, "exclude_folders": None}
    wf.update(over)
    return wf


@pytest.mark.parametrize("dest,conv,depth", [("jpeg", "jxl_to_jpeg_force", None),
                                             ("png", "jxl_to_png", 8)])
def test_direct_lossy_decode_with_delete_is_refused_before_the_token(
        menu, tmp_path, dest, conv, depth):
    assert not menu.execute_workflow(_wf(tmp_path, dest, conv, bit_depth=depth), STATUS)
    assert menu._calls["token"] == 0 and menu._calls["child"] == []


def test_direct_16bit_png_with_delete_still_runs(menu, tmp_path):
    assert menu.execute_workflow(_wf(tmp_path, "png", "jxl_to_png", bit_depth=16), STATUS)
    assert "--delete-source" in menu._calls["child"][0]


def test_manifest_lossy_decode_row_drops_the_delete(menu, tmp_path):
    wf = _wf(tmp_path, "jpeg", "jxl_to_jpeg_force", mode=99)
    cmd = menu._build_manifest_entry_cmd(
        str(SCRIPTS / "jxl_jpeg_transcoder.py"), wf["input_dir"], wf["input_dir"], 1,
        "jxl", "jpeg", 2, wf, wf["advanced_options"])
    assert "--delete-source" not in cmd, cmd


@pytest.mark.parametrize("flags", ["--mode 8", "--mode=8", "--mod 8"])
def test_expert_mode_flag_is_refused(menu, tmp_path, flags):
    """W-B: the run never starts, and no token is charged."""
    wf = _wf(tmp_path, "jxl", "", dest_format="jxl", expert_flags=flags,
             advanced_options={})
    assert not menu.execute_workflow(wf, STATUS)
    assert menu._calls["token"] == 0 and menu._calls["child"] == []


def test_expert_modular_flag_is_not_mistaken_for_mode():
    assert wp._flags_set_mode("--modular on --effort 9") is None
