#!/usr/bin/env python3
"""Regressions for the priority-4 audit fixes (polish):

  * W5 — a fractional Mode cell ("7.5") was silently truncated to mode 7 by
    int(float()); it is now refused like any other invalid value.
  * W6 — _parse_child_summary's int() coercions raised ValueError on a
    wrong-typed field, killing a finished manifest run at the summary block.
  * W8 — a manifest with mode-8 rows never got --delete-source from the
    wizard (the mode table promises "DELETE originals"), and the user was
    never asked. The wizard now asks and marks delete_source.
  * W9 — expert-flag deletion charged the wrapper's HHMM token but left the
    child's OWN confirmation active, re-prompting on an invisible stdin.
    _append_expert_flags now pairs it with --delete-confirm-off.
  * W10 — comment rows could poison the Direction guard; a folder literally
    named "source" was eaten as the CSV header; stems were lowercased
    unconditionally (false positives on case-sensitive filesystems).
  * D4 — a marked 2-page group (1 real page + 1 thumbnail) decoded with
    --thumbnail-handling ignore kept the _page<N> suffix in the output name.
  * T4 — the staging sweep logged "KEEP in staging (md5_fail)" for a file
    the worker had already deleted.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_tiff_decoder as dec
import jxl_jpeg_transcoder as tr
import jxl_photo as wp


@pytest.fixture
def menu(tmp_path, monkeypatch):
    """A menu on a throwaway config — never the user's real one."""
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _manifest(tmp_path, text):
    p = tmp_path / "m.csv"
    p.write_text(text, encoding="utf-8-sig")
    return str(p)


# ---------------------------------------------------------------------------
# W5 — fractional Mode cell
# ---------------------------------------------------------------------------

def test_fractional_mode_cell_is_refused(menu, tmp_path, capsys):
    src = tmp_path / "a"
    src.mkdir()
    entries = menu._load_manifest_entries(
        _manifest(tmp_path, f"Source,Destination,Mode,Direction\n{src},,7.5,tiff2jxl\n"),
        "tiff", "jxl")
    assert entries is None
    assert "Invalid Mode value" in capsys.readouterr().out


def test_excel_style_integral_mode_still_loads(menu, tmp_path):
    src = tmp_path / "a"
    src.mkdir()
    entries = menu._load_manifest_entries(
        _manifest(tmp_path, f"Source,Destination,Mode,Direction\n{src},,7.0,tiff2jxl\n"),
        "tiff", "jxl")
    assert entries == [(str(src), str(src), 7)]


# ---------------------------------------------------------------------------
# W6 — malformed summary fields degrade to 0
# ---------------------------------------------------------------------------

def test_parse_child_summary_tolerates_wrong_types(menu):
    line = '##JXLSUM## {"ok": "many", "overwritten": null, "skipped": 3, "errors": 1}'
    clean = menu._parse_child_summary(line)
    assert clean is not None
    assert clean["ok"] == 0
    assert clean["skipped"] == 3
    assert clean["errors"] == 1


# ---------------------------------------------------------------------------
# W8 — mode-8 manifest rows: the wizard asks about deletion
# ---------------------------------------------------------------------------

def _mode8_manifest(tmp_path):
    src = tmp_path / "shoot"
    src.mkdir()
    return _manifest(tmp_path,
                     f"Source,Destination,Mode,Direction\n{src},{src},8,tiff2jxl\n")


def test_wizard_manifest_mode8_asks_and_marks_delete(menu, tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    monkeypatch.setattr(wp.InteractiveMenu, "_pick_manifest",
                        lambda self: _mode8_manifest(tmp_path))
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_manifest_entries",
                        lambda self, p, e: True)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    workflow = {"origin_format": "tiff", "dest_format": "jxl"}
    assert menu._wizard_run_from_manifest(workflow) is True
    assert workflow.get("delete_source") is True


def test_wizard_manifest_mode8_decline_keeps_sources(menu, tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    monkeypatch.setattr(wp.InteractiveMenu, "_pick_manifest",
                        lambda self: _mode8_manifest(tmp_path))
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_manifest_entries",
                        lambda self, p, e: True)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    workflow = {"origin_format": "tiff", "dest_format": "jxl"}
    assert menu._wizard_run_from_manifest(workflow) is True
    assert not workflow.get("delete_source")


# ---------------------------------------------------------------------------
# W9 — expert-flag deletion pairs with --delete-confirm-off
# ---------------------------------------------------------------------------

def test_expert_flag_delete_gets_confirm_off():
    cmd = ["child.py"]
    wp._append_expert_flags(cmd, "--delete-so --effort 9")
    assert cmd.count("--delete-confirm-off") == 1
    assert cmd[-1] == "--delete-confirm-off"


def test_expert_flag_delete_confirm_off_not_duplicated():
    cmd = ["child.py"]
    wp._append_expert_flags(cmd, "--delete-source --delete-confirm-off")
    assert cmd.count("--delete-confirm-off") == 1


def test_harmless_expert_flags_get_nothing_extra():
    cmd = ["child.py"]
    wp._append_expert_flags(cmd, "--effort 9")
    assert "--delete-confirm-off" not in cmd


# ---------------------------------------------------------------------------
# W10 — comment-row Direction, "source" folder, normcase stems
# ---------------------------------------------------------------------------

def test_comment_row_direction_does_not_poison_guard(menu, tmp_path):
    src = tmp_path / "a"
    src.mkdir()
    entries = menu._load_manifest_entries(
        _manifest(tmp_path,
                  "Source,Destination,Mode,Direction\n"
                  "#note,,6,jxl2tiff\n"
                  f"{src},,0,tiff2jxl\n"),
        "tiff", "jxl")
    assert entries == [(str(src), str(src), 0)], \
        "a comment row's Direction cell refused a valid manifest"


def test_folder_named_source_is_not_eaten_as_header(menu, tmp_path):
    # A RELATIVE path whose whole first cell is literally "source": the old
    # header check compared only cell 0 and dropped the row.
    entries = menu._load_manifest_entries(
        _manifest(tmp_path, "source,,0,tiff2jxl\nother,,0,tiff2jxl\n"),
        "tiff", "jxl")
    assert entries is not None and len(entries) == 2, \
        "first data row dropped because its folder is named 'source'"


def test_real_header_row_still_skipped(menu, tmp_path):
    src = tmp_path / "a"
    src.mkdir()
    entries = menu._load_manifest_entries(
        _manifest(tmp_path, f"Source,Destination,Mode,Direction\n{src},,0,tiff2jxl\n"),
        "tiff", "jxl")
    assert entries == [(str(src), str(src), 0)]


# ---------------------------------------------------------------------------
# D4 — marked group shrunk by thumbnail-ignore sheds the _page<N> suffix
# ---------------------------------------------------------------------------

def test_shrunk_marked_group_strips_page_suffix():
    anchor = Path("scan_page1.jxl")
    one_real_entry = [(anchor, 1, False, False, 0, False, None)]
    out = dec._group_naming_path(anchor, one_real_entry, was_marked_group=True)
    assert out.name == "scan.jxl"


def test_standalone_page_named_file_still_kept():
    lone = Path("photo_page2.jxl")
    entries = [(lone, 0, False, False, 0, False, None)]
    assert dec._group_naming_path(lone, entries).name == "photo_page2.jxl"
    # ...and an explicit was_marked_group=False must keep it too.
    assert dec._group_naming_path(lone, entries, False).name == "photo_page2.jxl"


# ---------------------------------------------------------------------------
# T4 — no KEEP log for a file the worker already deleted
# ---------------------------------------------------------------------------

def test_no_keep_log_when_worker_deleted_bad_output(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8fake")
    final = tmp_path / "photo.jxl"

    tr.setup_logger()
    monkeypatch.setattr(tr, "TEMP2_DIR", str(staging))
    warnings = []
    monkeypatch.setattr(tr.logger, "warning", lambda m, *a: warnings.append(str(m)))

    # md5_fail: the worker deleted the staged output itself, so nothing
    # exists at write_out by the time the sweep runs.
    monkeypatch.setattr(tr, "encode_one_transcode",
                        lambda *a, **k: (str(src), "md5_fail", "mismatch", None))
    tr.process_group_transcode([(src, final)], 1, False, False, 1, False, False)
    assert not any("KEEP in staging" in w for w in warnings), \
        f"KEEP logged for a file that no longer exists: {warnings}"
