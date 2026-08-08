#!/usr/bin/env python3
"""Bug #290 — the round-29 low-severity findings, fixed as one batch.

None of these destroys data. They are here because each one makes the tool lie
a little: a flag that does nothing without saying so, a simulation that leaves
folders behind, a summary that contradicts itself, a delete gate that names a
folder the run will never write to.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_photo as wp
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
ENCODER = str(REPO / "jxl_tiff_encoder.py")
DECODER = str(REPO / "jxl_tiff_decoder.py")
TRANSCODER = str(REPO / "jxl_jpeg_transcoder.py")
BACKENDS = [enc, dec, tr]
IDS = ["encoder", "decoder", "transcoder"]


def _run(script, *args, cwd):
    return subprocess.run([sys.executable, script, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=600, cwd=str(cwd), stdin=subprocess.DEVNULL)


def _tiff(path: Path, value=1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((32, 32, 3), value, np.uint16),
                     photometric="rgb")


# ── the provenance marker lookup is case-insensitive on paths ───────────────

@pytest.mark.parametrize("mod", BACKENDS, ids=IDS)
def test_marker_lookup_survives_a_recased_path(mod, tmp_path, monkeypatch):
    """exiftool can hand back a differently-cased drive letter. A lookup miss
    left both markers None, which reads downstream as "no marker at all" — so a
    file whose provenance is perfectly recorded was refused as "written by an
    older version", a reason that sends the user after the wrong problem."""
    out = tmp_path / "Out" / "Photo.JXL"
    out.parent.mkdir()
    out.write_bytes(b"x")

    payload = [{"SourceFile": str(out).upper(),
                "Relation": ["jxlphoto-src:abc123", "jxlphoto-srcsum:def456"]}]

    class _R:
        returncode = 0
        stdout = __import__("json").dumps(payload)
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _R())
    got = mod._read_source_markers_batch([out])[str(out)]
    assert got == {"src": "abc123", "srcsum": "def456"}


# ── a dry run creates nothing ───────────────────────────────────────────────

def test_encoder_dry_run_does_not_create_the_staging_dir(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    staging = tmp_path / "never"
    r = _run(ENCODER, "src", "--mode", "0", "--distance", "0", "--dry-run",
             "--staging", str(staging), cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert not staging.exists(), "the dry run created its staging folder"


def test_decoder_dry_run_does_not_create_the_staging_dir(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    assert _run(ENCODER, "src", "jxl", "--mode", "2", "--distance", "0",
                cwd=tmp_path).returncode == 0
    staging = tmp_path / "never"
    r = _run(DECODER, "jxl", "out", "--mode", "2", "--dry-run",
             "--staging", str(staging), cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert not staging.exists()


def test_a_real_run_still_creates_it(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    staging = tmp_path / "stg"
    assert _run(ENCODER, "src", "--mode", "0", "--distance", "0",
                "--staging", str(staging), cwd=tmp_path).returncode == 0
    assert staging.exists()


# ── flags that do nothing must say so ───────────────────────────────────────

def test_provenance_without_delete_source_warns(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    r = _run(ENCODER, "src", "--mode", "5", "--distance", "0",
             "--provenance", "content", cwd=tmp_path)
    assert "has no effect without --delete-source" in r.stdout, r.stdout


def test_no_adopt_scan_without_adopt_warns(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    r = _run(ENCODER, "src", "--mode", "5", "--distance", "0", "--delete-source",
             "--delete-confirm-off", "--provenance", "path", "--no-adopt-scan",
             cwd=tmp_path)
    assert "--no-adopt-scan has no effect" in r.stdout, r.stdout


def test_provenance_in_a_non_collapsing_mode_warns(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    r = _run(ENCODER, "src", "--mode", "3", "--distance", "0", "--delete-source",
             "--delete-confirm-off", "--provenance", "content", cwd=tmp_path)
    assert "has no effect in mode 3" in r.stdout, r.stdout


def test_the_decoder_warns_too(tmp_path):
    _tiff(tmp_path / "src" / "a.tif")
    assert _run(ENCODER, "src", "jxl", "--mode", "2", "--distance", "0",
                cwd=tmp_path).returncode == 0
    r = _run(DECODER, "jxl", "out", "--mode", "5", "--provenance", "content",
             cwd=tmp_path)
    assert "has no effect without --delete-source" in r.stdout, r.stdout


# ── the dry-run summary must not contradict its own failure list ────────────

def test_dry_run_summary_counts_the_refusals_it_lists(tmp_path):
    """errors:0 next to a non-empty `failures` made the wrapper's recap
    disagree with itself. The exit code stays 0 — a simulation does not fail."""
    import json
    _tiff(tmp_path / "root" / "A" / "foto.tif", 1000)
    assert _run(ENCODER, "root", "--mode", "5", "--distance", "0", "--delete-source",
                "--delete-confirm-off", cwd=tmp_path).returncode == 0
    _tiff(tmp_path / "root" / "B" / "foto.tif", 60000)

    r = _run(ENCODER, "root", "--mode", "5", "--distance", "0", "--delete-source",
             "--delete-confirm-off", "--dry-run", "--summary-json", cwd=tmp_path)
    assert r.returncode == 0, "a dry run must still exit 0"
    line = next(l for l in r.stdout.splitlines()
                if l.lstrip().startswith(enc.SUMMARY_PREFIX))
    summary = json.loads(line.lstrip()[len(enc.SUMMARY_PREFIX):])
    assert summary["dry_run"] is True
    assert summary["failures"], "the refusal was not listed"
    assert summary["errors"] == len(summary["failures"])


# ── per-run counters really are per-run ─────────────────────────────────────

@pytest.mark.parametrize("mod", BACKENDS, ids=IDS)
def test_delete_stats_reset_with_the_abort_latch(mod):
    """A second run in the same process inherited the first one's totals."""
    mod._delete_stats["deleted"] = 7
    mod._delete_stats["kept"] = 3
    mod._reset_abort()
    assert all(v == 0 for v in mod._delete_stats.values()), mod._delete_stats


def test_the_transcoder_does_not_log_a_zeroed_delete_summary_at_startup():
    src = (REPO / "jxl_jpeg_transcoder.py").read_text(encoding="utf-8")
    setup = src[src.index("def setup_logger("):src.index("def setup_logger(") + 1400]
    assert "_log_delete_summary()" not in setup


# ── the content id is hashed once per source, not once per page ─────────────

@pytest.mark.parametrize("mod", BACKENDS, ids=IDS)
def test_the_content_id_is_memoised_per_source(mod, tmp_path):
    """A three-page 700 MB scan asked for its source's id once per PAGE."""
    f = tmp_path / "big.tif"
    f.write_bytes(b"y" * 8192)
    mod._content_id_cache.clear()

    reads = {"n": 0}
    real_open = open

    def _counting_open(path, *a, **k):
        if str(path) == str(f):
            reads["n"] += 1
        return real_open(path, *a, **k)

    import builtins
    orig = builtins.open
    builtins.open = _counting_open
    try:
        first = mod._file_content_id(f)
        for _ in range(4):
            assert mod._file_content_id(f) == first
    finally:
        builtins.open = orig
    assert reads["n"] == 1, f"the source was read {reads['n']} times"


@pytest.mark.parametrize("mod", BACKENDS, ids=IDS)
def test_a_source_edited_mid_run_is_not_served_a_stale_hash(mod, tmp_path):
    import os
    import time
    f = tmp_path / "x.bin"
    f.write_bytes(b"a" * 100)
    mod._content_id_cache.clear()
    before = mod._file_content_id(f)
    time.sleep(0.01)
    f.write_bytes(b"b" * 200)
    os.utime(f, None)
    assert mod._file_content_id(f) != before


# ── the delete gate must name the folder the run will actually write to ─────

@pytest.mark.parametrize("origin,dest,folder", [
    ("tiff", "jxl", "16B_JXL"),
    ("jxl", "tiff", "16B_TIFF"),
    ("jpeg", "jxl", "JXL_jpeg"),
    ("jxl", "jpeg", "JPEG_recovered"),
])
def test_export_folder_name_matches_the_child_constant(origin, dest, folder):
    assert wp._export_folder_name(origin, dest) == folder


def test_the_children_still_use_those_names():
    assert enc.EXPORT_JXL_FOLDER == wp._export_folder_name("tiff", "jxl")
    assert dec.EXPORT_TIFF_FOLDER == wp._export_folder_name("jxl", "tiff")
    assert tr.EXPORT_JXL_FOLDER == wp._export_folder_name("jpeg", "jxl")
    assert tr.EXPORT_JPEG_FOLDER == wp._export_folder_name("jxl", "jpeg")


# ── a hand-edited config must not take the menus down ───────────────────────

def _config_with(tmp_path, monkeypatch, presets):
    import json
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"presets": presets}), encoding="utf-8")
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: path)
    return wp.ConfigManager()


def test_a_list_of_presets_is_ignored_not_fatal(tmp_path, monkeypatch, capsys):
    cfg = _config_with(tmp_path, monkeypatch, ["a", "b"])
    assert cfg.config.presets == {}
    assert "not a mapping" in capsys.readouterr().out


def test_a_preset_saved_as_a_string_is_dropped(tmp_path, monkeypatch, capsys):
    cfg = _config_with(tmp_path, monkeypatch, {"good": {"last_workers": 4}, "bad": "oops"})
    assert list(cfg.config.presets) == ["good"], "the good preset was lost too"
    assert "malformed preset" in capsys.readouterr().out


def test_the_preset_menu_survives_it(tmp_path, monkeypatch):
    cfg = _config_with(tmp_path, monkeypatch, {"good": {"last_workers": 4}, "bad": 7})
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    # _describe_session is what crashed on a non-dict body.
    for name, body in cfg.config.presets.items():
        assert isinstance(menu._describe_session(body), str)
