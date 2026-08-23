#!/usr/bin/env python3
"""Regressions for the round-33 low-severity wrapper batch.

None of these destroys data; each makes the wrapper lie a little or die ugly:

  1. A JXL->TIFF preset advertised `q=95` — a knob the decoder never receives.
  2. execute_workflow charged the HHMM delete token and pre-created the mode-2
     output folder BEFORE noticing the child script is not installed.
  3. `--delete-source=1` was read as a delete request, but the children's
     store_true argparse rejects an explicit argument (exit 2) — a token
     charged for a run that never starts.
  4. `--list-presets` (read-only) was unreachable with no codecs installed.
  5. _session_number_error validated only numbers; a hand-edited provenance /
     multipage_mode / compression / ... reached the child's argparse as a
     command line the user never typed.
  6. _ask_delete_options wrote its answers straight into the live config, so
     cancelling the wizard still persisted them via the next unrelated save.
  7. A hand-written manifest with Mode=7 and a Source ABOVE the marker ran
     with an empty --export-subfolder: mode 6 wearing a mode-7 label.
  8. The mode-2 mkdir died with a raw traceback on an uncreatable folder.
  9. Ctrl+C in a manifest run skipped the summary block and traceback'd out.
 10. A hand-edited config storing the NUMBER 99 bypassed the manifest-repeat
     label/disable logic, which compared against the STRING "99".
"""

import sys
import unittest.mock as mock
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A ConfigManager pointed at a throwaway file — never the user's real one."""
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    return wp.ConfigManager()


@pytest.fixture
def menu(cfg):
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


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


# ─────────────────────────────────────────────
# 1. A decode-to-TIFF preset must not advertise a quality it never uses
# ─────────────────────────────────────────────

def test_decode_to_tiff_preset_shows_no_quality(menu):
    """#310 fixed the distance side; the decoder direction still showed the q=
    of whatever JPEG run came before it (save_last_session only overwrites
    last_quality when a run supplies one)."""
    session = {
        "last_origin_format": "jxl", "last_dest_format": "tiff",
        "last_conversion_type": "jxl_tiff_decoder",
        "last_output_mode": "6", "last_input_dir": "G:\\x",
        "last_workers": 8, "last_quality": 95, "last_distance": 0.05,
    }
    line = wp.InteractiveMenu._describe_session(session)
    assert "q=" not in line
    assert "d=" not in line


def test_jxl_to_jpeg_preset_still_shows_quality(menu):
    session = {
        "last_origin_format": "jxl", "last_dest_format": "jpeg",
        "last_conversion_type": "jxl_to_jpeg_auto",
        "last_output_mode": "0", "last_input_dir": "G:\\x",
        "last_workers": 8, "last_quality": 95, "last_distance": 0.05,
    }
    assert "q=95" in wp.InteractiveMenu._describe_session(session)


# ─────────────────────────────────────────────
# 2. The missing-script check runs before the gates, not after them
# ─────────────────────────────────────────────

def _tiff_workflow(tmp_path, **over):
    wf = {
        "origin_format": "tiff", "dest_format": "jxl", "mode": 2,
        "input_dir": str(tmp_path), "workers": 4, "effort": 7,
        "distance": 0.1, "use_ram": False, "staging": None,
        "advanced_options": {"delete_source": True},
        "expert_flags": "", "mode_config": {"output_dir": str(tmp_path / "out")},
        "dry_run": False,
    }
    wf.update(over)
    return wf


def test_missing_script_is_refused_before_the_token_and_the_mkdir(menu, tmp_path, monkeypatch):
    """The HHMM token is a proof of presence for a delete run; charging it —
    or pre-creating the output folder — for a script that is not installed is
    work done for a run that can never start."""
    monkeypatch.setattr(wp, "SCRIPT_DIR", tmp_path / "nowhere")
    charged = []
    monkeypatch.setattr(menu, "_confirm_archive_mode", lambda: charged.append(1) or True)

    ok = menu.execute_workflow(_tiff_workflow(tmp_path), STATUS)

    assert ok is False
    assert charged == [], "the HHMM token was charged for a script that does not exist"
    assert not (tmp_path / "out").exists(), "the mode-2 folder was pre-created anyway"


# ─────────────────────────────────────────────
# 3. --delete-source=1 is refused before any token is charged
# ─────────────────────────────────────────────

def test_delete_flag_with_a_value_is_not_a_delete_request():
    """store_true flags take no argument: argparse exits 2 on `--delete-source=1`,
    so it can never delete anything — and must not charge the HHMM token."""
    assert wp._flags_request_delete("--delete-source=1") is False
    assert wp._flags_request_delete("--delete-source") is True


def test_delete_flag_with_a_value_is_reported_for_refusal():
    assert wp._flags_ambiguous_delete("--delete-source=1") == "--delete-source=1"
    assert wp._flags_ambiguous_delete("--delete-skipped=0") == "--delete-skipped=0"
    # A bare abbreviation is still a real delete request, not a refusal.
    assert wp._flags_ambiguous_delete("--delete-source") is None


def test_valued_delete_flag_is_refused_before_the_token(menu, tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "SCRIPT_DIR", tmp_path / "nowhere")
    charged = []
    monkeypatch.setattr(menu, "_confirm_archive_mode", lambda: charged.append(1) or True)
    wf = _tiff_workflow(tmp_path, expert_flags="--delete-source=1")

    ok = menu.execute_workflow(wf, STATUS)

    assert ok is False
    assert charged == [], "an argparse-rejected flag still charged the HHMM token"


# ─────────────────────────────────────────────
# 4. --list-presets needs no codecs
# ─────────────────────────────────────────────

def _config_with_preset(tmp_path, name="nightly", **preset):
    import json
    data = {
        "last_input_dir": str(tmp_path),
        "dependencies_checked": True,
        "presets": {name: {
            "last_input_dir": str(tmp_path),
            "last_output_mode": "6",
            "last_origin_format": "tiff",
            "last_dest_format": "jxl",
            "last_conversion_type": "jxl_tiff_encoder",
            "last_workers": 12,
            "last_distance": 0.05,
            **preset,
        }},
    }
    path = tmp_path / ".jxl_tools_config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_list_presets_works_without_codecs(tmp_path, monkeypatch, capsys):
    """Listing what is saved is read-only; the cjxl/djxl gate used to exit 1
    before it on a machine with neither codec installed."""
    cfg_path = _config_with_preset(tmp_path)
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: cfg_path)
    monkeypatch.setattr(wp.DependencyChecker, "check_dependencies",
                        lambda self, force=False: {"cjxl": False, "djxl": False})
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py", "--list-presets"])
    with pytest.raises(SystemExit) as exit_info:
        wp.main()
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "nightly" in out and "workers 12" in out


# ─────────────────────────────────────────────
# 5. The corrupt-session validation covers the enumerated fields
# ─────────────────────────────────────────────

@pytest.mark.parametrize("field,value,word", [
    ("last_bit_depth", 12, "bit depth"),
    ("last_bit_depth", "8.5", "bit depth"),
    ("last_provenance", "md5", "provenance"),
    ("last_multipage_mode", "explode", "multipage_mode"),
    ("last_compression", "rar", "compression"),
    ("last_depth_policy", "force8", "depth_policy"),
    ("last_conversion_type", "jxl_to_bmp", "conversion_type"),
    ("last_icc_profile", "AdobeRGB", "icc_profile"),
])
def test_corrupt_enum_fields_are_refused(field, value, word):
    err = wp._session_number_error(_session(**{field: value}))
    assert err is not None and word in err


@pytest.mark.parametrize("field,value", [
    ("last_bit_depth", 8),
    ("last_bit_depth", "16"),
    ("last_provenance", "adopt"),
    ("last_multipage_mode", "split_all"),
    ("last_compression", "lzw"),
    ("last_depth_policy", "preserve_original"),
    ("last_conversion_type", "jxl_tiff_encoder_lossless"),
    ("last_icc_profile", "sRGB"),
])
def test_legit_enum_fields_pass(field, value):
    assert wp._session_number_error(_session(**{field: value})) is None


# ─────────────────────────────────────────────
# 6. The delete-gate answers are staged, not written to the live config
# ─────────────────────────────────────────────

def test_delete_options_do_not_mutate_the_config(menu, cfg, monkeypatch):
    """Cancelling the wizard after these questions used to leave the answers
    in the in-memory config, persisted by the next unrelated save_config()."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    # verify? y — delete already-converted? n — match by: <enter> (path)
    monkeypatch.setattr("builtins.input", mock.Mock(side_effect=["y", "n", ""]))
    workflow = {"origin_format": "tiff", "dest_format": "jxl"}

    menu._ask_delete_options(workflow, collapses=True, scope_label="Mode 5 drops folder structure")

    assert cfg.config.last_verify_roundtrip is None
    assert cfg.config.last_delete_skipped is None
    assert cfg.config.last_provenance is None
    staged = workflow.get("_pending_session_fields")
    assert staged == {"last_verify_roundtrip": True, "last_delete_skipped": False,
                      "last_provenance": "path", "last_adopt_scan": None}


# ─────────────────────────────────────────────
# 7. Mode-7 manifest entries get their subfolder, or fail safe
# ─────────────────────────────────────────────

def _manifest_menu(tmp_path, monkeypatch, cfg):
    """A menu whose SCRIPT_DIR holds the (empty) child scripts and whose
    combined log goes to the throwaway folder."""
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    for name in ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py"):
        (scripts / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(wp, "SCRIPT_DIR", scripts)
    monkeypatch.setattr(wp, "WRAPPER_LOG_DIR", tmp_path / "logs")
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _manifest_workflow(entries, **over):
    wf = {
        "origin_format": "tiff", "dest_format": "jxl",
        "conversion_type": "jxl_tiff_encoder", "workers": 2,
        "manifest_entries": entries, "manifest_path": "m.csv",
        "mode_config": {}, "advanced_options": {}, "dry_run": False,
        "distance": 0.1, "effort": 7, "use_ram": False, "staging": None,
        "expert_flags": "",
    }
    wf.update(over)
    return wf


def test_mode7_manifest_derives_the_subfolder_from_the_source(tmp_path, monkeypatch, cfg):
    """Auto-generated manifests bake the subfolder into the Source itself
    (<marker>/<sub>); the run must hand it back to the child."""
    menu = _manifest_menu(tmp_path, monkeypatch, cfg)
    calls = []
    monkeypatch.setattr(menu, "_run_subprocess", lambda cmd: calls.append(list(map(str, cmd))) or 0)
    source = tmp_path / "2024_EXPORT" / "16B_TIFF"
    source.mkdir(parents=True)
    wf = _manifest_workflow([(str(source), str(source), 7)])

    assert menu._execute_manifest_workflow(wf, STATUS) is True
    assert len(calls) == 1
    cmd = calls[0]
    assert "--export-subfolder" in cmd
    assert cmd[cmd.index("--export-subfolder") + 1] == "16B_TIFF"


def test_mode7_manifest_above_the_marker_is_refused_unattended(tmp_path, monkeypatch, cfg):
    """A hand-written Mode=7 whose Source is ABOVE the marker cannot name its
    subfolder; with an empty --export-subfolder the child runs as mode 6."""
    menu = _manifest_menu(tmp_path, monkeypatch, cfg)
    calls = []
    monkeypatch.setattr(menu, "_run_subprocess", lambda cmd: calls.append(cmd) or 0)
    source = tmp_path / "shoot"   # no marker in the path: the marker is below it
    source.mkdir()
    wf = _manifest_workflow([(str(source), str(source), 7)], unattended=True)

    assert menu._execute_manifest_workflow(wf, STATUS) is False
    assert calls == [], "an underivable mode-7 entry ran with mode-6 semantics"


def test_mode7_manifest_above_the_marker_warns_and_can_be_declined(tmp_path, monkeypatch, cfg):
    menu = _manifest_menu(tmp_path, monkeypatch, cfg)
    calls = []
    monkeypatch.setattr(menu, "_run_subprocess", lambda cmd: calls.append(cmd) or 0)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    source = tmp_path / "shoot"
    source.mkdir()
    wf = _manifest_workflow([(str(source), str(source), 7)])

    assert menu._execute_manifest_workflow(wf, STATUS) is False
    assert calls == []


# ─────────────────────────────────────────────
# 8. An uncreatable mode-2 folder is a clean error, not a traceback
# ─────────────────────────────────────────────

def test_mode2_mkdir_failure_is_a_clean_error(menu, tmp_path, monkeypatch, capsys):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "jxl_tiff_encoder.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(wp, "SCRIPT_DIR", scripts)
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file", encoding="utf-8")   # mkdir below it must fail
    wf = _tiff_workflow(tmp_path, advanced_options={},
                        mode_config={"output_dir": str(blocker / "out")})

    ok = menu.execute_workflow(wf, STATUS)   # the bug was an uncaught OSError

    assert ok is False
    assert "Cannot create output folder" in capsys.readouterr().out


# ─────────────────────────────────────────────
# 9. Ctrl+C keeps the manifest accounting and exits cleanly
# ─────────────────────────────────────────────

def test_ctrlc_in_a_manifest_still_renders_the_summary(tmp_path, monkeypatch, cfg):
    menu = _manifest_menu(tmp_path, monkeypatch, cfg)
    summaries = []
    monkeypatch.setattr(menu, "_render_manifest_summary",
                        lambda reports, ok, skip, err: summaries.append(reports))

    def _boom(cmd):
        raise KeyboardInterrupt

    monkeypatch.setattr(menu, "_run_subprocess", _boom)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    wf = _manifest_workflow([(str(a), str(a), 6), (str(b), str(b), 6)])

    with pytest.raises(KeyboardInterrupt):
        menu._execute_manifest_workflow(wf, STATUS)

    assert summaries, "Ctrl+C skipped the end-of-run accounting entirely"
    states = [r["state"] for r in summaries[0]]
    assert states == ["cancelled", "not started"]


def test_ctrlc_exits_main_with_130(tmp_path, monkeypatch):
    """A KeyboardInterrupt out of a run used to traceback through main()."""
    cfg_path = _config_with_preset(tmp_path)
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: cfg_path)
    monkeypatch.setattr(wp.DependencyChecker, "check_dependencies",
                        lambda self, force=False: {"cjxl": True, "djxl": True})
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)

    def _boom(self, wf, st):
        raise KeyboardInterrupt

    monkeypatch.setattr(wp.InteractiveMenu, "execute_workflow", _boom)
    monkeypatch.setattr(sys, "argv", ["jxl_photo.py"])
    # Menu "2" (repeat), then the repeat's prompts up to execution.
    monkeypatch.setattr("builtins.input", mock.Mock(side_effect=["2", "", "", "", "0"]))

    with pytest.raises(SystemExit) as exit_info:
        wp.main()
    assert exit_info.value.code == 130


# ─────────────────────────────────────────────
# 10. The manifest-repeat label survives a NUMERIC 99 in the config
# ─────────────────────────────────────────────

def _write_manifest(path, rows, direction="tiff2jxl"):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Source", "Destination", "Mode", "Direction"])
        for src, dst, mode in rows:
            w.writerow([src, dst, mode, direction])
    return str(path)


def test_numeric_99_is_still_a_manifest_repeat(tmp_path, monkeypatch, capsys):
    """Hand-edited JSON (or Excel) stores the NUMBER 99; a string-only ==
    bypassed the manifest label and the missing-CSV disable logic."""
    import json
    src = tmp_path / "shoot"
    src.mkdir()
    manifest = _write_manifest(tmp_path / "manifest_x.csv", [(str(src), str(src), 6)])
    data = {
        "last_input_dir": str(tmp_path),
        "last_output_mode": 99,   # the NUMBER, not the string
        "last_manifest_path": manifest,
        "last_origin_format": "tiff",
        "last_dest_format": "jxl",
        "last_conversion_type": "jxl_tiff_encoder",
        "dependencies_checked": True,
    }
    cfg_path = tmp_path / ".jxl_tools_config.json"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path", lambda self: cfg_path)
    cm = wp.ConfigManager()
    menu = wp.InteractiveMenu(cm, wp.DependencyChecker(cm))
    with mock.patch.object(wp, "RICH_AVAILABLE", False), \
         mock.patch("builtins.input", side_effect=["2"]):
        choice = menu.show_main_menu(True)
    assert choice == "2"
    out = capsys.readouterr().out
    assert "manifest:" in out, "a numeric 99 was offered as a plain mode-99 repeat"
