#!/usr/bin/env python3
"""Modes 6/7 leave out the DERIVATIVES below the export marker, by marker.

The folder skip of #565 knows the other tools' folders by the names in their
settings. A derivative folder named only on a command line
(`--export-jxl-folder 16B_JXL_sRGB`, a preset's `MOBILE`) was still taken by
the decoder's, the recompressor's and the transcoder's JPEG/PNG decode modes
6/7: next to its master, with the master's name, it landed on the same output
and the run stopped on the duplicate (S1/S2 of the 261103 review of delivery
A). Every derivative carries `jxlphoto-derived:<recipe>`, so the three now
leave those out (mode 6, or mode 7 without an explicit subfolder); the
wrapper's manifest collision walk re-checks the files that collide.

Pre-fix proof: JXL_SCRIPTS_UNDER_TEST=<the five scripts of the base commit>.
"""

import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_SCRIPTS_UNDER_TEST")
sys.path.insert(0, str(Path(_OVERRIDE).resolve()) if _OVERRIDE else str(_REPO))

import jxl_jpeg_transcoder as tra  # noqa: E402
import jxl_photo as wp  # noqa: E402
import jxl_recompressor as rec  # noqa: E402
import jxl_tiff_decoder as dec  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

EVENT = "250118_吾妻山公園_菜の花 [FINAL] 5%"
NAME = "DSC_0001 (2).jxl"
CUSTOM = "16B_JXL_sRGB (web)"        # a derivative folder only a command line named


def _stub(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)
    return path


def _tree(tmp_path):
    exp = tmp_path / EVENT / "01_Z7" / enc.EXPORT_MARKER
    return (_stub(exp / enc.EXPORT_JXL_FOLDER / NAME), _stub(exp / CUSTOM / NAME))


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, m, *a, **k): self.lines.append(str(m))
    warning = error = debug = info


# ---------------------------------------------------------------- recompressor

def test_recompressor_drops_derivative_inputs(tmp_path, monkeypatch):
    master, deriv = _tree(tmp_path)
    monkeypatch.setattr(rec, "logger", _Log())
    monkeypatch.setattr(rec, "_read_derived_markers_batch",
                        lambda paths: {str(p): ("sRGB" if str(p) == str(deriv) else None)
                                       for p in paths})
    assert rec._drop_derivative_inputs([master, deriv]) == [master]


def test_recompressor_keeps_an_unreadable_input(tmp_path, monkeypatch):
    """A marker that cannot be read is no proof of a derivative: kept."""
    master, deriv = _tree(tmp_path)
    monkeypatch.setattr(rec, "logger", _Log())
    monkeypatch.setattr(rec, "_read_derived_markers_batch",
                        lambda paths: {str(p): False for p in paths})
    assert rec._drop_derivative_inputs([master, deriv]) == [master, deriv]


# ---------------------------------------------------------------- decoder

def _fake_multipage(deriv):
    def reader(jxls):
        out = {}
        for j in jxls:
            info = {'group': None, 'inherited': False, 'subfiletype': 0,
                    'grayscale': False, 'depth': None, 'page': None, 'pages': None,
                    'thumb': False, 'srcsum': None, 'extrasamples': None,
                    'tifftags': None, 'derived': str(j) == str(deriv)}
            out[str(j)] = info
        return out
    return reader


def test_decoder_reader_flags_the_derived_marker(tmp_path, monkeypatch):
    master, deriv = _tree(tmp_path)

    class _R:
        returncode = 0
        stderr = ""
        stdout = json.dumps([
            {"SourceFile": master.as_posix(), "Relation": ["jxlphoto-depth:16"]},
            {"SourceFile": deriv.as_posix(),
             "Relation": ["jxlphoto-depth:16", "jxlphoto-derived:sRGB/2048/d1e7"]}])
    monkeypatch.setattr(dec, "_run_captured", lambda *a, **k: _R())
    marks = dec._read_multipage_markers_batch([master, deriv])
    assert marks[str(deriv)].get("derived") is True
    assert not marks[str(master)].get("derived")


def test_decoder_groups_leave_out_derivatives_in_mode_6(tmp_path, monkeypatch):
    master, deriv = _tree(tmp_path)
    monkeypatch.setattr(dec, "logger", _Log())
    monkeypatch.setattr(dec, "_read_multipage_markers_batch", _fake_multipage(deriv))
    monkeypatch.setattr(dec, "_SKIP_DERIVED_INPUTS", True, raising=False)
    groups = dec.collect_multipage_groups([master, deriv])
    assert [str(k) for k in groups] == [str(master)], groups


def test_decoder_groups_keep_them_when_not_asked(tmp_path, monkeypatch):
    """Control: outside modes 6/7 (the flag off) a derivative decodes."""
    master, deriv = _tree(tmp_path)
    monkeypatch.setattr(dec, "logger", _Log())
    monkeypatch.setattr(dec, "_read_multipage_markers_batch", _fake_multipage(deriv))
    monkeypatch.setattr(dec, "_SKIP_DERIVED_INPUTS", False, raising=False)
    groups = dec.collect_multipage_groups([master, deriv])
    assert len(groups) == 2


# ---------------------------------------------------------------- transcoder

@pytest.mark.parametrize("mode,sub,kept", [(6, "", 1), (7, "", 1), (7, CUSTOM, 2)])
def test_transcoder_drops_derivative_pairs(tmp_path, monkeypatch, mode, sub, kept):
    master, deriv = _tree(tmp_path)
    monkeypatch.setattr(tra, "logger", _Log())
    monkeypatch.setattr(tra, "EXPORT_JPEG_SUBFOLDER", sub)
    tra._DERIVED_INPUT_LABELS.clear()
    monkeypatch.setattr(tra, "_read_derived_markers_batch",
                        lambda paths: {str(p): ("sRGB" if str(p) == str(deriv) else None)
                                       for p in paths})
    pairs = [(master, tmp_path / "a.jpg"), (deriv, tmp_path / "b.jpg")]
    assert len(tra._drop_derivative_pairs(pairs, mode)) == kept


# ---------------------------------------------------------------- wrapper

def _menu():
    menu = wp.InteractiveMenu.__new__(wp.InteractiveMenu)
    menu.config = wp.ConfigManager()
    return menu


def test_wrapper_collision_walk_drops_a_derivative_collision(tmp_path, monkeypatch):
    master, deriv = _tree(tmp_path)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch", _fake_multipage(deriv))
    collisions = _menu()._manifest_output_collisions(
        [(str(tmp_path / EVENT), str(tmp_path / "unused [dest]"), 6)], {".jxl"},
        origin="jxl", dest="tiff")
    assert collisions == [], collisions


def test_wrapper_collision_walk_keeps_a_real_collision(tmp_path, monkeypatch):
    """Control: two masters (no derived marker) still collide."""
    master, deriv = _tree(tmp_path)
    monkeypatch.setattr(dec, "_read_multipage_markers_batch", _fake_multipage(None))
    collisions = _menu()._manifest_output_collisions(
        [(str(tmp_path / EVENT), str(tmp_path / "unused [dest]"), 6)], {".jxl"},
        origin="jxl", dest="tiff")
    assert len(collisions) == 1, collisions


def test_recompressor_mode6_dry_run_no_longer_aborts_on_the_pair(tmp_path, monkeypatch):
    """End to end in main(): master + derivative with the same name below the
    marker used to stop the run on the duplicate output (exit 2)."""
    master, deriv = _tree(tmp_path)
    captured = {}
    monkeypatch.setattr(rec, "setup_logger", lambda *a, **k: tmp_path / "x.log")
    monkeypatch.setattr(rec, "logger", _Log())
    monkeypatch.setattr(rec, "emit_summary_json", lambda *a, **k: captured.update(k))
    monkeypatch.setattr(rec, "_read_derived_markers_batch",
                        lambda paths: {str(p): ("sRGB" if str(p) == str(deriv) else None)
                                       for p in paths})
    monkeypatch.setattr(rec, "_read_encode_params_batch",
                        lambda paths: {str(p): {"desc": "", "software": "",
                                                "params": None, "gen": 0}
                                       for p in paths})
    for name in ("DELETE_SOURCE", "DELETE_SKIPPED", "OVERWRITE"):
        monkeypatch.setattr(rec, name, getattr(rec, name))
    monkeypatch.setattr(sys, "argv", ["jxl_recompressor.py", str(tmp_path / EVENT),
                                      "--mode", "6", "--dry-run", "--summary-json",
                                      "--on-unknown", "convert"])
    try:
        rec.main()
    except SystemExit as e:
        assert e.code != 2, "the run stopped on the duplicate output"
    assert captured, "the run ended before its summary"
