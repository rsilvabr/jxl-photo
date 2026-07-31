#!/usr/bin/env python3
"""Regressions for corrupt numbers in a saved workflow (preset / repeat).

`.jxl_tools_config.json` is plain JSON the user can hand-edit and _load_config
does no type checking, so a stored workflow can come back with nonsense in it.
Before this:

  * `last_output_mode: "sete"` reached `int()` inside _run_saved_session and
    ended the run in a raw ValueError traceback, exit 1 — after the settings
    panel had already been drawn;
  * `last_workers` / `last_effort` / `last_quality` were str()'d onto the child
    command line, so the CHILD's argparse rejected a command line the user
    never typed;
  * `last_distance` was the only one with a clamp helper (_sane_distance) and
    the preset path was the one caller that did not use it.

Corrupt numbers are refused rather than defaulted: replaying a workflow with a
mode nobody chose would write the outputs somewhere else entirely.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp


STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (calls.append(list(map(str, cmd))), 0)[1])
    return calls


def _session(**over):
    s = {n: None for n in wp.ToolConfig.__dataclass_fields__ if n.startswith("last_")}
    s.update({
        "last_output_mode": "0", "last_workers": 4, "last_effort": 7,
        "last_distance": 0.1, "last_origin_format": "tiff", "last_dest_format": "jxl",
        "last_conversion_type": "jxl_tiff_encoder",
        "last_advanced_options": {"overwrite": False, "sync": True},
    })
    s.update(over)
    return s


# --------------------------------------------------------------------------
# _as_exact_int / _session_number_error
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [(7, 7), ("7", 7), (7.0, 7), ("7.0", 7), (0, 0)])
def test_exact_int_accepts_integral_values(raw, expected):
    assert wp._as_exact_int(raw) == expected


@pytest.mark.parametrize("raw", ["sete", "7.5", 7.5, "", None, [], float("nan"), float("inf")])
def test_exact_int_refuses_the_rest(raw):
    with pytest.raises((ValueError, TypeError)):
        wp._as_exact_int(raw)


@pytest.mark.parametrize("field,value,word", [
    ("last_output_mode", "sete", "mode"),
    ("last_output_mode", "42", "mode"),
    ("last_output_mode", "7.5", "mode"),
    ("last_workers", "muitos", "workers"),
    ("last_workers", 0, "workers"),
    ("last_workers", 999, "workers"),
    ("last_effort", 11, "effort"),
    ("last_quality", 0, "quality"),
])
def test_corrupt_numbers_are_reported(field, value, word):
    err = wp._session_number_error(_session(**{field: value}))
    assert err is not None and word in err


@pytest.mark.parametrize("session_over", [
    {}, {"last_output_mode": "99"}, {"last_output_mode": "8"},
    {"last_workers": 32}, {"last_effort": 1}, {"last_quality": 100},
    # Absent values are not corrupt values.
    {"last_workers": None, "last_effort": None, "last_quality": None},
])
def test_sane_sessions_pass(session_over):
    assert wp._session_number_error(_session(**session_over)) is None


def test_a_wild_distance_is_not_treated_as_corrupt():
    """Distance clamps, it does not refuse — that decision predates this check."""
    assert wp._session_number_error(_session(last_distance="0,05")) is None


# --------------------------------------------------------------------------
# End to end through _run_saved_session
# --------------------------------------------------------------------------

def test_corrupt_mode_refuses_instead_of_crashing(menu, launched, tmp_path, capsys):
    src = tmp_path / "photos"
    src.mkdir()
    session = _session(last_input_dir=str(src), last_output_mode="sete")

    # The bug was an uncaught ValueError, so "does not raise" is the assertion.
    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})

    assert ok is False
    assert launched == []
    out = " ".join(capsys.readouterr().out.split())
    assert "corrupt" in out and "mode is not a number" in out


def test_corrupt_workers_refuses_before_reaching_the_child(menu, launched, tmp_path, capsys):
    src = tmp_path / "photos"
    src.mkdir()
    session = _session(last_input_dir=str(src), last_workers="muitos")

    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})

    assert ok is False
    assert launched == [], "a bogus --workers was handed to the child"
    assert "workers is not a number" in " ".join(capsys.readouterr().out.split())


def test_unparseable_distance_is_clamped_not_passed_through(menu, launched, tmp_path):
    """"0,05" must never reach the cjxl command line."""
    src = tmp_path / "photos"
    src.mkdir()
    session = _session(last_input_dir=str(src), last_distance="0,05")

    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})

    assert ok is True
    assert len(launched) == 1
    cmd = launched[0]
    assert "0,05" not in cmd
    assert "--distance" in cmd
    assert float(cmd[cmd.index("--distance") + 1]) == 0.1  # _sane_distance fallback


def test_a_healthy_preset_is_untouched(menu, launched, tmp_path):
    src = tmp_path / "photos"
    src.mkdir()
    session = _session(last_input_dir=str(src), last_workers=8,
                       last_effort=9, last_distance=0.05)

    ok = menu._run_saved_session(session, STATUS,
                                 answers={"overwrite": False, "dry_run": False})

    assert ok is True
    cmd = launched[0]
    assert cmd[cmd.index("--workers") + 1] == "8"
    assert cmd[cmd.index("--effort") + 1] == "9"
    assert float(cmd[cmd.index("--distance") + 1]) == 0.05
