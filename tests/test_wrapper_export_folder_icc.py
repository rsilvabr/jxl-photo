#!/usr/bin/env python3
"""Wrapper: `--export-jxl-folder`, `--output-icc` and `--rename-from/-to`.

The wrapper has to mirror the children's flags in three places that used to
drift: the command builders, the modes 6/7 folder used by the delete preview
and the manifest collision mirror (which imports the child in-process and reads
the same module globals).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo as wp

REPO = Path(__file__).resolve().parent.parent


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _capture_direct_cmd(monkeypatch, workflow):
    menu = _menu()
    cmds = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (cmds.append(cmd), 0)[1])
    assert menu.execute_workflow(workflow, {"cjxl": True})
    assert cmds, "no child command was built"
    return cmds[0]


def _tiff_to_jxl_workflow(tmp_path, **mode_config):
    return {
        "origin_format": "tiff", "dest_format": "jxl", "mode": 7,
        "input_dir": str(tmp_path), "workers": 2, "effort": 7,
        "distance": 0.05, "use_ram": True, "staging": None,
        "dry_run": False, "mode_config": mode_config,
        "advanced_options": {"sync": True},
        "conversion_type": "jxl_tiff_encoder",
    }


def _jxl_to_jxl_workflow(tmp_path, **overrides):
    workflow = {
        "origin_format": "jxl", "dest_format": "jxl", "mode": 7,
        "input_dir": str(tmp_path), "workers": 2, "effort": 7,
        "distance": 1.0, "staging": None, "dry_run": False,
        "mode_config": {"export_subfolder": "16B_JXL",
                        "export_jxl_folder": "16B_JXL_sRGB"},
        "advanced_options": {"sync": True, "output_icc": "sRGB"},
        "conversion_type": "jxl_recompress",
    }
    workflow.update(overrides)
    return workflow


# ── command builders ────────────────────────────────────────────────────────

def test_direct_tiff_to_jxl_emits_the_export_folder(tmp_path, monkeypatch):
    cmd = _capture_direct_cmd(monkeypatch, _tiff_to_jxl_workflow(
        tmp_path, export_subfolder="TIFF16", export_jxl_folder="PRINT_JXL"))
    i = cmd.index("--export-jxl-folder")
    assert cmd[i + 1] == "PRINT_JXL"


def test_direct_jxl_to_jxl_emits_icc_and_folder(tmp_path, monkeypatch):
    cmd = _capture_direct_cmd(monkeypatch, _jxl_to_jxl_workflow(tmp_path))
    i = cmd.index("--output-icc")
    assert cmd[i + 1] == "sRGB"
    i = cmd.index("--export-jxl-folder")
    assert cmd[i + 1] == "16B_JXL_sRGB"


def test_direct_jxl_to_jxl_emits_the_rename(tmp_path, monkeypatch):
    wf = _jxl_to_jxl_workflow(tmp_path)
    wf["advanced_options"] = {"sync": True, "output_icc": "sRGB",
                              "rename_from": "ProPhoto", "rename_to": "sRGB"}
    cmd = _capture_direct_cmd(monkeypatch, wf)
    i = cmd.index("--rename-from")
    assert cmd[i + 1] == "ProPhoto"
    assert cmd[i + 2] == "--rename-to"
    assert cmd[i + 3] == "sRGB"


def test_direct_jxl_to_tiff_never_emits_the_folder(tmp_path, monkeypatch):
    wf = {
        "origin_format": "jxl", "dest_format": "tiff", "mode": 7,
        "input_dir": str(tmp_path), "workers": 2, "effort": 7,
        "compression": "zip", "bit_depth": 16, "staging": None,
        "dry_run": False, "add_preview": True,
        "mode_config": {"export_subfolder": "16B_JXL",
                        "export_jxl_folder": "PRINT_JXL"},
        "advanced_options": {"sync": True},
        "conversion_type": "jxl_to_tiff",
    }
    cmd = _capture_direct_cmd(monkeypatch, wf)
    assert "--export-jxl-folder" not in cmd


def test_manifest_entry_cmd_tiff_to_jxl(tmp_path):
    menu = _menu()
    wf = _tiff_to_jxl_workflow(tmp_path, export_subfolder="TIFF16",
                               export_jxl_folder="PRINT_JXL")
    cmd = menu._build_manifest_entry_cmd(
        "script.py", str(tmp_path), str(tmp_path), 7,
        "tiff", "jxl", 2, wf, wf["advanced_options"])
    i = cmd.index("--export-jxl-folder")
    assert cmd[i + 1] == "PRINT_JXL"


def test_manifest_entry_cmd_jxl_to_jxl(tmp_path):
    menu = _menu()
    wf = _jxl_to_jxl_workflow(tmp_path)
    wf["advanced_options"] = {"sync": True, "output_icc": "sRGB",
                              "rename_from": "ProPhoto", "rename_to": "sRGB"}
    cmd = menu._build_manifest_entry_cmd(
        "script.py", str(tmp_path), str(tmp_path), 7,
        "jxl", "jxl", 2, wf, wf["advanced_options"])
    assert "--output-icc" in cmd and cmd[cmd.index("--output-icc") + 1] == "sRGB"
    assert "--export-jxl-folder" in cmd
    i = cmd.index("--rename-from")
    assert cmd[i + 1] == "ProPhoto" and cmd[i + 3] == "sRGB"


def test_manifest_entry_cmd_jxl_to_tiff_never_emits_the_folder(tmp_path):
    menu = _menu()
    wf = {
        "origin_format": "jxl", "dest_format": "tiff", "mode": 7,
        "compression": "zip", "bit_depth": 16,
        "mode_config": {"export_jxl_folder": "PRINT_JXL"},
        "advanced_options": {},
    }
    cmd = menu._build_manifest_entry_cmd(
        "script.py", str(tmp_path), str(tmp_path), 7,
        "jxl", "tiff", 2, wf, {})
    assert "--export-jxl-folder" not in cmd


# ── _with_child_marker ──────────────────────────────────────────────────────

def test_with_child_marker_applies_and_restores_the_folder():
    import jxl_recompressor as rec
    before = rec.EXPORT_JXL_FOLDER
    with wp._with_child_marker(rec, None, None, "X"):
        assert rec.EXPORT_JXL_FOLDER == "X"
    assert rec.EXPORT_JXL_FOLDER == before

    with pytest.raises(RuntimeError):
        with wp._with_child_marker(rec, None, None, "Y"):
            assert rec.EXPORT_JXL_FOLDER == "Y"
            raise RuntimeError("boom")
    assert rec.EXPORT_JXL_FOLDER == before


def test_count_origin_files_honors_the_custom_folder(tmp_path):
    """The recompressor's recursive finder skips its own output folder: a
    custom EXPORT_JXL_FOLDER must be applied while the preview counts."""
    import jxl_recompressor as rec
    before = rec.EXPORT_JXL_FOLDER
    root = tmp_path
    for name in ("_EXPORT/16B_JXL", "_EXPORT/16B_JXL_sRGB"):
        (root / name).mkdir(parents=True)
        (root / name / "a.jxl").write_bytes(
            b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)
    menu = _menu()
    workflow = {"origin_format": "jxl", "dest_format": "jxl",
                "input_dir": str(root),
                "mode_config": {"export_subfolder": "16B_JXL",
                                "export_jxl_folder": "16B_JXL_sRGB"}}
    assert menu._count_origin_files(workflow, 7) == 1
    assert rec.EXPORT_JXL_FOLDER == before, "the preview leaked its folder"


# ── preset replay ───────────────────────────────────────────────────────────

def test_saved_session_replays_the_export_folder(tmp_path, monkeypatch):
    menu = _menu()
    captured = []
    monkeypatch.setattr(wp.InteractiveMenu, "execute_workflow",
                        lambda self, wf, st: (captured.append(wf), True)[1])
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    session = {
        "last_input_dir": str(tmp_path),
        "last_output_mode": "7",
        "last_origin_format": "jxl",
        "last_dest_format": "jxl",
        "last_conversion_type": "jxl_recompress",
        "last_workers": 4,
        "last_effort": 7,
        "last_distance": 1.0,
        "last_mode_config": {"export_marker": "_EXPORT",
                             "export_subfolder": "16B_JXL",
                             "export_jxl_folder": "16B_JXL_sRGB"},
    }
    assert menu._run_saved_session(session, {"cjxl": True})
    assert captured[0]["mode_config"]["export_jxl_folder"] == "16B_JXL_sRGB"


# ── guards ──────────────────────────────────────────────────────────────────

def test_execute_workflow_refuses_icc_in_place(tmp_path, monkeypatch):
    menu = _menu()
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda *a, **k: pytest.fail("the child must not run"))
    wf = _jxl_to_jxl_workflow(tmp_path, mode=8)
    assert menu.execute_workflow(wf, {"cjxl": True}) is False


def test_execute_workflow_refuses_rename_in_place(tmp_path, monkeypatch):
    menu = _menu()
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda *a, **k: pytest.fail("the child must not run"))
    wf = _jxl_to_jxl_workflow(tmp_path, mode=8)
    wf["advanced_options"] = {"rename_from": "ProPhoto", "rename_to": "sRGB"}
    assert menu.execute_workflow(wf, {"cjxl": True}) is False


# ── collision mirror ────────────────────────────────────────────────────────

def _stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


def test_manifest_collision_mirror_sees_the_rename(tmp_path):
    menu = _menu()
    a, b, out = tmp_path / "a", tmp_path / "b", tmp_path / "out"
    a.mkdir(), b.mkdir(), out.mkdir()
    _stub(a / "a_ProPhoto.jxl")
    _stub(b / "a_sRGB.jxl")
    entries = [(str(a), str(out), 2), (str(b), str(out), 2)]
    collisions = menu._manifest_output_collisions(
        entries, {".jxl"}, origin="jxl", dest="jxl",
        rename_from="ProPhoto", rename_to="sRGB")
    assert collisions, "the renamed collision went unseen"
    assert not menu._manifest_output_collisions(
        entries, {".jxl"}, origin="jxl", dest="jxl")


# ── stale derivative answers (Step 6) and manifest in-place rows ────────────

_SEEDED = {"output_icc": "sRGB", "rename_from": "ProPhoto", "rename_to": "sRGB"}


def _step6_workflow(tmp_path, mode):
    return {
        "origin_format": "jxl", "dest_format": "jxl", "mode": mode,
        "conversion_type": "jxl_recompress", "input_dir": str(tmp_path),
        "workers": 2, "effort": 7, "distance": 1.0, "quality": 95,
        "staging": None, "bit_depth": 16, "compression": "zip",
        "use_ram": True, "dry_run": False, "mode_config": {},
        # An earlier pass through Step 6 answered the derivative questions.
        "advanced_options": dict(_SEEDED),
    }


def _answer_rich(monkeypatch, colour_space):
    def ask(prompt, *a, **k):
        if "Output colour space" in str(prompt):
            return colour_space
        if "Target distance" in str(prompt):
            return "1.0"
        return k.get("default", "")
    monkeypatch.setattr(wp.Prompt, "ask", staticmethod(ask))
    monkeypatch.setattr(wp.IntPrompt, "ask",
                        staticmethod(lambda *a, **k: int(k.get("default", 1))))
    monkeypatch.setattr(wp.Confirm, "ask",
                        staticmethod(lambda *a, **k: bool(k.get("default", False))))


@pytest.mark.skipif(not wp.RICH_AVAILABLE, reason="rich branch")
@pytest.mark.parametrize("mode", [7, 8])
def test_step6_keep_or_in_place_drops_every_derivative_answer_rich(tmp_path, monkeypatch, mode):
    """'keep' used to drop only output_icc — the rename rode along into a plain
    recompression; modes 0/8 dropped nothing, so the execute guard refused a
    run for an option the step never offered."""
    _answer_rich(monkeypatch, "keep")
    wf = _step6_workflow(tmp_path, mode)
    _menu()._wizard_parameters_basic(wf, {"cjxl": True})
    adv = wf["advanced_options"]
    assert not any(k in adv for k in _SEEDED), adv


@pytest.mark.parametrize("mode", [7, 8])
def test_step6_keep_or_in_place_drops_every_derivative_answer_plain(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")   # every default; colour = keep
    wf = _step6_workflow(tmp_path, mode)
    _menu()._wizard_parameters_basic(wf, {"cjxl": True})
    adv = wf["advanced_options"]
    assert not any(k in adv for k in _SEEDED), adv


def test_derivative_in_place_rows():
    rows = wp._derivative_in_place_rows([
        ("C:/a", "C:/a", 0),       # Destination = Source -> in place
        ("C:/b", "", 0),           # empty Destination -> in place
        ("C:/c", "C:/out", 0),     # another folder -> fine
        ("C:/d", "C:/d", 8),       # mode 8 -> in place
        ("C:/e", "C:/e", 7),       # mode 7 computes its own folder -> fine
    ])
    assert [r[0] for r in rows] == ["C:/a", "C:/b", "C:/d"]


@pytest.mark.parametrize("adv", [{"output_icc": "sRGB"},
                                 {"rename_from": "ProPhoto", "rename_to": "sRGB"}])
def test_manifest_refuses_in_place_rows_up_front(tmp_path, monkeypatch, adv):
    """A manifest row in mode 8 (or mode 0 with Destination = Source) used to
    reach the child and fail with exit 2 mid-run; now the whole manifest is
    refused before any child starts."""
    menu = _menu()
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda *a, **k: pytest.fail("the child must not run"))
    src = tmp_path / "src"
    src.mkdir()
    wf = _jxl_to_jxl_workflow(tmp_path, mode=99)
    wf["advanced_options"] = dict(adv)
    wf["manifest_entries"] = [(str(src), str(tmp_path / "out"), 2),
                              (str(src), str(src), 8)]
    assert menu.execute_workflow(wf, {"cjxl": True}) is False


def test_manifest_refuses_icc_with_delete(tmp_path, monkeypatch):
    menu = _menu()
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda *a, **k: pytest.fail("the child must not run"))
    src = tmp_path / "src"
    src.mkdir()
    wf = _jxl_to_jxl_workflow(tmp_path, mode=99)
    wf["advanced_options"] = {"output_icc": "sRGB", "delete_source": True}
    wf["manifest_entries"] = [(str(src), str(tmp_path / "out"), 2)]
    assert menu.execute_workflow(wf, {"cjxl": True}) is False
