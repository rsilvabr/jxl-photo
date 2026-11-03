#!/usr/bin/env python3
"""W-A of the 2026-11-03 audit: planning-time exiftool batch reads say they
are still moving, and the wrapper's idle limit is a setting.

The planning reads (400 files per exiftool call) printed nothing until they
finished. On a busy disk one batch takes minutes (#537), so a large library
spent an hour in silence and the wrapper — which stops a child that prints
nothing for 60 min — reported a hung child, every scheduled run. Every batch
reader now calls _batch_read_tick between batches (parity-pinned, quiet
while fast, same pacing as _scan_tick), and the wrapper reads its limit from
the config field child_idle_timeout_minutes.

Pre-fix proof (must fail on the base commit's scripts):

    mkdir <tmp>/wapre
    git show HEAD:<each of the five scripts> > <tmp>/wapre/<same name>
    JXL_SCRIPTS_UNDER_TEST=<tmp>/wapre python -m pytest tests/test_batch_read_progress.py -q
"""

import importlib
import os
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
sys.path.insert(0, str(Path(_OVERRIDE).resolve()) if _OVERRIDE else str(_REPO))

import jxl_photo as wp  # noqa: E402

HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"

# (script, reader, label) — every planning-time batch reader but the
# recompressor's encode-record read, which reported progress already.
READERS = [
    ("jxl_tiff_encoder", "_read_source_markers_batch", "Provenance markers"),
    ("jxl_tiff_encoder", "_read_page_markers_batch", "Page markers"),
    ("jxl_tiff_encoder", "_read_group_markers_batch", "Group markers"),
    ("jxl_tiff_decoder", "_read_source_markers_batch", "Provenance markers"),
    ("jxl_tiff_decoder", "_xmp_icc_jxls_batch", "Profile markers"),
    ("jxl_tiff_decoder", "_read_multipage_markers_batch", "Multipage markers"),
    ("jxl_jpeg_transcoder", "_read_source_markers_batch", "Provenance markers"),
    ("jxl_jpeg_transcoder", "_read_derived_markers_batch", "Derivative markers"),
    ("jxl_jpeg_transcoder", "_read_thumb_markers_batch", "Thumbnail markers"),
    ("jxl_recompressor", "_read_source_markers_batch", "Provenance markers"),
    ("jxl_recompressor", "_read_derived_markers_batch", "Derivative markers"),
    ("jxl_recompressor", "_read_mpg_markers", "Multipage markers"),
]


class _Run:
    def __init__(self, stdout="[]", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class _FakeLogger:
    def __init__(self):
        self.infos = []

    def info(self, m, *a, **k): self.infos.append(str(m))
    def warning(self, m, *a, **k): pass
    def error(self, m, *a, **k): pass
    def debug(self, m, *a, **k): pass


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("w") / HOSTILE / "16B_JXL (2)"
    d.mkdir(parents=True)
    out = []
    for n in range(801):          # three batches of 400
        f = d / f"DSC_{n:04d} [x].jxl"
        f.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n")
        out.append(f)
    return out


@pytest.mark.parametrize("script,reader,label", READERS,
                         ids=[f"{s}.{r}" for s, r, _l in READERS])
def test_slow_batch_read_reports_between_batches(script, reader, label, files,
                                                 monkeypatch):
    mod = importlib.import_module(script)
    fake = _FakeLogger()
    monkeypatch.setattr(mod, "logger", fake)
    monkeypatch.setattr(mod, "_run_captured", lambda *a, **k: _Run())
    # "Slow": the quiet window is already over when the first batch ends.
    monkeypatch.setattr(mod, "_SCAN_QUIET_SECONDS", -1.0)
    monkeypatch.setattr(mod, "_SCAN_REPORT_EVERY", 0.0)
    getattr(mod, reader)(list(files))
    lines = [m for m in fake.infos if m.strip().startswith(f"{label}:")]
    assert lines, f"{script}.{reader} printed no progress: {fake.infos}"
    assert lines[0].strip().startswith(f"{label}: 400/801 read"), lines
    assert any("800/801" in m for m in lines), lines


@pytest.mark.parametrize("script", ["jxl_tiff_encoder", "jxl_tiff_decoder",
                                    "jxl_jpeg_transcoder", "jxl_recompressor"])
def test_fast_batch_read_stays_quiet(script, files, monkeypatch):
    """Control: a read that finishes inside the quiet window prints nothing."""
    mod = importlib.import_module(script)
    fake = _FakeLogger()
    monkeypatch.setattr(mod, "logger", fake)
    monkeypatch.setattr(mod, "_run_captured", lambda *a, **k: _Run())
    mod._read_source_markers_batch(list(files))
    assert not [m for m in fake.infos if "Provenance markers:" in m], fake.infos


# ---------------------------------------------------------------------------
# The wrapper's idle limit
# ---------------------------------------------------------------------------

def _menu(minutes):
    cfg = wp.ConfigManager.__new__(wp.ConfigManager)
    cfg.config = wp.ToolConfig()
    if minutes is not None:
        cfg.config.child_idle_timeout_minutes = minutes
    menu = wp.InteractiveMenu.__new__(wp.InteractiveMenu)
    menu.config = cfg
    return menu


@pytest.mark.parametrize("minutes,seconds", [(None, 3600), (90, 5400), (2, 300),
                                             ("abc", 3600), (True, 3600)])
def test_idle_limit_comes_from_the_config(minutes, seconds):
    assert _menu(minutes)._child_idle_timeout() == seconds


def test_stream_child_uses_the_configured_limit(monkeypatch):
    menu = _menu(None)
    monkeypatch.setattr(menu, "_child_idle_timeout", lambda: 1)
    monkeypatch.setattr(menu, "_print_error", lambda *a, **k: None)
    t0 = time.monotonic()
    rc = menu._stream_child([sys.executable, "-c", "import time; time.sleep(20)"])
    assert rc == -1
    assert time.monotonic() - t0 < 15, "the configured limit was not used"


def test_old_config_without_the_field_loads(tmp_path, monkeypatch):
    """A config file written before the field existed keeps working, and a
    hand-edited value is read back."""
    import json
    cfg_file = tmp_path / "設定 [old].json"
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: cfg_file)
    cfg_file.write_text(json.dumps({"default_workers": 3}), encoding="utf-8")
    assert wp.ConfigManager().config.child_idle_timeout_minutes == 60
    cfg_file.write_text(json.dumps({"child_idle_timeout_minutes": 180}), encoding="utf-8")
    menu = wp.InteractiveMenu.__new__(wp.InteractiveMenu)
    menu.config = wp.ConfigManager()
    assert menu._child_idle_timeout() == 180 * 60
