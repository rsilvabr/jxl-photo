"""The wizard's note before the HHMM token of a TIFF → JXL deleting run
(MASTER_MAX_DISTANCE, 2.31.1-dev3): which classes of TIFF the encoder will
keep because their distance is above its limit. The limit is the encoder's
own setting, read in process — never a copy in the wizard. No codec.
"""
import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import jxl_photo as wp  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402


def _job(distance=1.0, **adv):
    wf = {"origin_format": "tiff", "dest_format": "jxl", "advanced_options": dict(adv)}
    if distance is not None:
        wf["distance"] = distance
    return wf


def _note(wf):
    return getattr(wp, "_master_limit_note")(wf)


@pytest.fixture(autouse=True)
def _shipped_classes(monkeypatch):
    monkeypatch.setattr(enc, "SCAN_DISTANCE", 0.0)
    monkeypatch.setattr(enc, "EIGHT_BIT_DISTANCE", "auto")


def test_note_names_the_classes_above_the_childs_limit():
    note = _note(_job(1.0))
    assert note and "MASTER_MAX_DISTANCE" in note
    assert ": TIFFs (distance 1), 8-bit TIFFs (distance 1)." in note, note
    assert "film scans" not in note


def test_no_note_within_the_limit():
    assert _note(_job(0.05)) is None


def test_the_limit_is_the_childs_setting_never_a_copy(monkeypatch):
    monkeypatch.setattr(enc, "MASTER_MAX_DISTANCE", 5.0, raising=False)
    assert _note(_job(1.0)) is None
    monkeypatch.setattr(enc, "MASTER_MAX_DISTANCE", 0.02, raising=False)
    note = _note(_job(0.05))
    assert note and "0.02" in note, note
    monkeypatch.setattr(enc, "MASTER_MAX_DISTANCE", None, raising=False)
    assert _note(_job(1.0)) is None
    monkeypatch.delattr(enc, "MASTER_MAX_DISTANCE", raising=False)
    assert _note(_job(1.0)) is None


def test_scan_and_eight_bit_rows_count():
    note = _note(_job(0.05, scan_distance="2"))
    assert note and "film scans (distance 2)" in note, note
    assert "8-bit TIFFs" not in note and "TIFFs (distance 0.05)" not in note, note
    note = _note(_job(1.0, eight_bit_distance="same"))
    assert note and "8-bit TIFFs (distance 1)" in note, note


def test_other_directions_and_broken_jobs_get_no_note():
    wf = _job(1.0)
    wf["dest_format"] = "tiff"
    assert _note(wf) is None
    assert _note(_job(None)) is None


def test_a_job_without_a_distance_runs_at_the_childs_and_gets_its_note(monkeypatch):
    """Review N1: a job whose distance is None runs at the encoder's
    CJXL_DISTANCE — the note judges that one."""
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 1.0)
    note = _note(_job(None))
    assert note and "TIFFs (distance 1)" in note, note


def test_the_expert_flags_distances_count(monkeypatch):
    """Review N1: the expert flags come last on the child's command line and
    win; the note reads them with the child's own parse."""
    wf = _job(0.05)
    wf["expert_flags"] = "--distance 2"
    note = _note(wf)
    assert note and "TIFFs (distance 2)" in note, note
    wf = _job(1.0)
    wf["expert_flags"] = "--distance 0.05"
    assert _note(wf) is None
    wf = _job(0.05)
    wf["expert_flags"] = "--scan-distance 3"
    note = _note(wf)
    assert note and "film scans (distance 3)" in note, note
    wf = _job(0.05)
    wf["expert_flags"] = "--eight-bit-distance 4"
    note = _note(wf)
    assert note and "8-bit TIFFs (distance 4)" in note, note


def test_a_deleting_job_shows_the_note_before_the_token(tmp_path, monkeypatch):
    """Review N4: the real execute_workflow, a ScriptedUI that answers the HHMM
    question wrong — the note is on the screen before the token is asked, and
    nothing is started."""
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    launched = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (launched.append(cmd), 0)[1])
    ui = wp.ScriptedUI({"gate.hhmm": "no"})
    menu._ui = ui
    src = tmp_path / "250118_吾妻山公園 [FINAL] 100%off %d" / "TIFF16"
    src.mkdir(parents=True)
    workflow = {
        'mode': 0, 'input_dir': str(src), 'workers': 1, 'effort': 7,
        'distance': 1.0, 'origin_format': 'tiff', 'dest_format': 'jxl',
        'conversion_type': 'jxl_tiff_encoder', 'use_ram': True,
        'advanced_options': {'overwrite': False, 'sync': True, 'delete_source': True},
    }
    status = {k: True for k in ("cjxl", "djxl", "exiftool", "magick", "tifffile",
                                "pillow", "imagecodecs")}
    assert menu.execute_workflow(workflow, status) is False
    assert ui.asked[-1] == "gate.hhmm", ui.asked
    assert launched == []
    text = [str(t) for t in ui.output]
    note_at = next(i for i, t in enumerate(text) if "MASTER_MAX_DISTANCE" in t)
    token_at = next(i for i, t in enumerate(text) if "to confirm" in t)
    assert note_at < token_at, text


def test_execute_workflow_prints_the_note_before_the_token():
    tree = ast.parse((REPO / "jxl_photo.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "InteractiveMenu")
    fn = next(f for f in cls.body if isinstance(f, ast.FunctionDef) and f.name == "execute_workflow")
    note = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "_master_limit_note"]
    token = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "_confirm_archive_mode"]
    assert note and token, (note, token)
    assert min(note) < min(token)
