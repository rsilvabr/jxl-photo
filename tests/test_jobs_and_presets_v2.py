"""Delivery C, part 1: Job, presets v2 and the migration of the saved runs.

A preset is saved as {"schema": 2, "job": {...}} PLUS the v1 snapshot
(last_* fields) generated from the job: the settings file is shared with
older copies of the toolkit (an execution copy, the Windows program), whose
--run-preset reads only last_*. The owner's six real presets (copied from
the config into tests/fixtures/owner_presets_v1.json, every path replaced by
<ROOT>; the manifests they run included) replay the same command lines after
the migration.

Pre-fix proof (fails on the beta, which has no Job):
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<tmp with git show f7cbcba:...>"
    python -m pytest tests/test_jobs_and_presets_v2.py -q
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
sys.path.insert(0, str(SCRIPTS))
import jxl_photo as wp  # noqa: E402

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}
FIXTURE = json.loads((REPO / "tests" / "fixtures" / "owner_presets_v1.json")
                     .read_text(encoding="utf-8"))
HOSTILE = "ルート [x] 5% (2)"


def _materialize(tmp_path):
    """The fixture's presets with <ROOT> made real: the manifests written and
    every folder a preset or a manifest row names created (hostile root)."""
    root = tmp_path / HOSTILE
    def real(v):
        if isinstance(v, str) and v.startswith("<ROOT>"):
            return str(root) + v[len("<ROOT>"):]
        if isinstance(v, dict):
            return {k: real(x) for k, x in v.items()}
        return v
    for mpath, text in FIXTURE["manifests"].items():
        p = Path(real(mpath))
        p.parent.mkdir(parents=True, exist_ok=True)
        text = text.replace("<ROOT>", str(root))
        p.write_text(text, encoding="utf-8")
        for line in text.splitlines()[1:]:
            for cell in line.split(",")[:2]:
                if cell.startswith(str(root)):
                    Path(cell).mkdir(parents=True, exist_ok=True)
    presets = {}
    for name, p in FIXTURE["presets"].items():
        p = real(p)
        for key in ("last_input_dir", "last_staging"):
            if p.get(key):
                Path(p[key]).mkdir(parents=True, exist_ok=True)
        presets[name] = p
    return presets


SYNTHETIC = {
    "mode 2 with an output folder": {
        "last_input_dir": "<IN>", "last_output_mode": "2", "last_workers": 3,
        "last_origin_format": "tiff", "last_dest_format": "jxl",
        "last_conversion_type": "jxl_tiff_encoder", "last_distance": 0.05,
        "last_mode_config": {"output_dir": "<OUT>"}, "last_exclude_folders": "old;tmp"},
    "mode 7 decode with a subfolder": {
        "last_input_dir": "<IN>", "last_output_mode": 7, "last_workers": 2,
        "last_origin_format": "jxl", "last_dest_format": "tiff",
        "last_conversion_type": "jxl_tiff_decoder", "last_bit_depth": 16,
        "last_mode_config": {"export_marker": "_EXPORT", "export_subfolder": "16B_JXL"},
        "last_advanced_options": {"overwrite": False, "sync": True, "depth_policy": "force16"}},
    "expert flags and extra options": {
        "last_input_dir": "<IN>", "last_output_mode": "1", "last_workers": 4,
        "last_origin_format": "jxl", "last_dest_format": "jpeg",
        "last_conversion_type": "jxl_to_jpeg_force", "last_quality": 90,
        "last_expert_flags": "--effort 5", "last_extra_options": {"output_name": "web [x]"},
        "last_icc_profile": "sRGB"},
}


def _synthetic(tmp_path):
    out = {}
    for name, s in SYNTHETIC.items():
        d = tmp_path / HOSTILE / name.replace(" ", "_")
        (d / "in").mkdir(parents=True, exist_ok=True)
        s = json.loads(json.dumps(s).replace("<IN>", str(d / "in").replace("\\", "\\\\"))
                       .replace("<OUT>", str(d / "out").replace("\\", "\\\\")))
        out[name] = s
    return out


def _all(tmp_path):
    return {**_materialize(tmp_path), **_synthetic(tmp_path)}


def _v1_fields(s):
    return {k: v for k, v in s.items() if k.startswith("last_") and v is not None}


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (calls.append(list(map(str, cmd))), 0)[1])
    return calls


def test_the_fixture_is_the_owners_six_presets():
    assert sorted(FIXTURE["presets"]) == sorted(
        ["SYNC PHOTOS", "OUTTAKES JPEG", "OUTTAKES JPEG + APAGAR", "SRGB", "MOBILE",
         "REBUILD MASTERS"])
    assert len(FIXTURE["manifests"]) == 4


def test_v1_to_job_and_back_is_lossless(tmp_path):
    for name, s in _all(tmp_path).items():
        job = wp.job_from_session_v1(s)
        assert wp.Job.from_dict(json.loads(json.dumps(job.to_dict()))) == job, name
        assert wp.session_v1_from_job(job) == _v1_fields(s), name


def test_tasks_of_the_owners_presets(tmp_path):
    tasks = {n: wp.job_from_session_v1(s).task for n, s in _materialize(tmp_path).items()}
    assert tasks == {"SYNC PHOTOS": "archive_tiff", "REBUILD MASTERS": "archive_tiff",
                     "OUTTAKES JPEG": "archive_jpeg", "OUTTAKES JPEG + APAGAR": "archive_jpeg",
                     "SRGB": "derive_jxl", "MOBILE": "derive_jxl"}


def test_a_v2_preset_carries_the_v1_fields_older_copies_read(tmp_path):
    for name, s in _all(tmp_path).items():
        v2 = wp._preset_v2(s)
        assert v2["schema"] == 2 and wp.Job.from_dict(v2["job"]).task, name
        assert _v1_fields(v2) == _v1_fields(s), name


def _replay(menu, launched, session):
    del launched[:]
    menu._run_saved_session(session, STATUS, answers={"overwrite": False, "dry_run": True})
    return [list(c) for c in launched]


def test_every_preset_replays_the_same_argv_as_v1_v2_and_for_an_older_copy(
        menu, launched, tmp_path):
    for name, s in _all(tmp_path).items():
        before = _replay(menu, launched, dict(s))
        assert before, f"{name}: nothing ran"
        v2 = wp._preset_v2(s)
        # This version: the job of the v2 preset.
        assert _replay(menu, launched, wp._preset_session(v2)) == before, name
        # An older copy: the whole v2 dict, read through its last_* fields.
        assert _replay(menu, launched, dict(v2)) == before, name


def test_save_preset_writes_schema_2_and_run_preset_reads_it(menu, launched, tmp_path):
    s = _materialize(tmp_path)["SRGB"]
    menu.config.save_preset("SRGB", s)
    saved = json.loads((tmp_path / ".jxl_tools_config.json").read_text(encoding="utf-8"))
    p = saved["presets"]["SRGB"]
    assert p["schema"] == 2 and p["job"]["task"] == "derive_jxl"
    assert "saved_at" in p
    assert _replay(menu, launched, wp._preset_session(p)) == _replay(menu, launched, s)


def test_a_job_that_cannot_be_read_falls_back_to_the_saved_fields(menu, launched,
                                                                  tmp_path, capsys):
    s = _materialize(tmp_path)["MOBILE"]
    v2 = wp._preset_v2(s)
    v2["job"]["mode"] = "sete"
    got = wp._preset_session(v2)
    assert "cannot be read" in capsys.readouterr().out
    assert got == _v1_fields(s)


def test_list_presets_describes_v1_and_v2_alike(tmp_path):
    for name, s in _materialize(tmp_path).items():
        assert (wp.InteractiveMenu._describe_session(wp._preset_session(wp._preset_v2(s)))
                == wp.InteractiveMenu._describe_session(s)), name
