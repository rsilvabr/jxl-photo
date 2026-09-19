#!/usr/bin/env python3
"""--modular on|off: the lossy-encoder choice, exposed to the CLI and the wrapper.

Measured 2026-09 on 7 real photos/scans (SSIMULACRA2 vs sRGB reference,
d=0.05/0.10, effort 7): modular lossy buys nothing for photos — quality a
wash (every margin <= 0.39), VarDCT smaller in 14/14 (modular up to +33%),
VarDCT 20-100x faster. So the default stays off (cjxl's VarDCT) and the
wrapper only asks inside Step 6A (advanced options); the flag exists for
screenshots/graphics batches.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo as wp
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE_TOOLS, reason="needs cjxl, djxl and exiftool on PATH")


def _menu(tmp_path, monkeypatch):
    """A menu on a throwaway config — never the user's real one."""
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _workflow(tmp_path, advanced):
    return {
        'mode': 2, 'advanced_options': advanced, 'dry_run': True,
        'origin_format': 'tiff', 'dest_format': 'jxl',
        'input_dir': str(tmp_path), 'workers': 2, 'compression': 'zip',
        'bit_depth': 16, 'mode_config': {}, 'expert_flags': '',
        'distance': 0.1, 'effort': 7, 'use_ram': True,
    }


def _captured_children(monkeypatch):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (calls.append(list(map(str, cmd))), 0)[1])
    return calls


def test_wrapper_passes_modular_on_to_the_encoder(tmp_path, monkeypatch):
    menu = _menu(tmp_path, monkeypatch)
    calls = _captured_children(monkeypatch)
    menu.execute_workflow(_workflow(tmp_path, {'modular': 'on'}), {})
    cmd = calls[0]
    assert '--modular' in cmd and cmd[cmd.index('--modular') + 1] == 'on', cmd


def test_wrapper_sends_no_modular_flag_by_default(tmp_path, monkeypatch):
    menu = _menu(tmp_path, monkeypatch)
    calls = _captured_children(monkeypatch)
    menu.execute_workflow(_workflow(tmp_path, {'overwrite': False, 'sync': True}), {})
    assert '--modular' not in calls[0], calls[0]


@real
def test_real_modular_flag_reaches_cjxl_and_decodes(tmp_path):
    """The flag is not a label: on/off must produce different bytes from real
    cjxl, the banner must say which encoder is active, and both outputs must
    decode — the mocked suite cannot see any of that."""
    import numpy as np
    import tifffile
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    arr = (np.random.default_rng(3).random((48, 64, 3)) * 65535).astype("uint16")
    tifffile.imwrite(src_dir / "photo.tif", arr, photometric="rgb")
    outputs = {}
    for flag, label in (("on", "modular"), ("off", "VarDCT")):
        out_dir = tmp_path / f"out_{flag}"
        r = subprocess.run(
            [sys.executable, str(REPO / "jxl_tiff_encoder.py"),
             str(src_dir), str(out_dir), "--mode", "2",
             "--distance", "1.0", "--workers", "1", "--modular", flag],
            capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"lossy/{label}" in r.stdout, r.stdout
        jxl = out_dir / "photo.jxl"
        assert jxl.exists()
        subprocess.run(["djxl", str(jxl), str(tmp_path / f"dec_{flag}.png")],
                       check=True, capture_output=True, timeout=300)
        outputs[flag] = jxl.read_bytes()
    assert outputs["on"] != outputs["off"], "on/off produced identical files — the flag did nothing"
