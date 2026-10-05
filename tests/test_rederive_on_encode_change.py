#!/usr/bin/env python3
"""Derivatives re-derived when distance/effort change (recompressor).

A derivative records its recipe AND the distance/effort it was encoded with
(`jxlphoto-derived:sRGB/d4e9`). `REDERIVE_ON_ENCODE_CHANGE` (default True) makes
a sync run re-derive an existing derivative whose recorded distance/effort
differ from this run's, exactly as it already does when the colour/size/
sharpening recipe changes. The real-codec test runs the whole
djxl -> magick -> cjxl pipeline against a synthetic master and drives six runs
in one temp folder, checking the recorded label and the file's mtime.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec
import jxl_photo as wp

REPO = Path(__file__).resolve().parent.parent

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool", "magick"))
real = pytest.mark.skipif(
    not _HAVE_TOOLS, reason="needs cjxl, djxl, exiftool and magick on PATH")


# ---------------------------------------------------------------------------
# T1 - unit: the suffix helpers
# ---------------------------------------------------------------------------

def test_encode_suffix_clamps_below_the_floor():
    assert rec._encode_suffix(4.0, 9, 0.05) == "/d4e9"
    assert rec._encode_suffix(0.01, 7, 0.05) == "/d0.05e7"
    assert rec._encode_suffix(0, 9, 0.05) == "/d0e9"


def test_split_encode_suffix():
    assert rec._split_encode_suffix("sRGB") == ("sRGB", None)
    assert rec._split_encode_suffix("keep@long320+screen/d1e7") == \
        ("keep@long320+screen", (1.0, 7))


@pytest.mark.parametrize("stored,wanted,check,expected", [
    ("sRGB/d1e7", "AdobeRGB/d1e7", True, "recipe changed"),
    ("sRGB", "AdobeRGB/d1e7", True, "recipe changed"),
    ("sRGB/d1e7", "sRGB/d2e7", True, "encode settings changed"),
    ("sRGB/d1e7", "sRGB/d1e9", True, "encode settings changed"),
    ("sRGB/d1e7", "sRGB/d2e7", False, None),
    ("sRGB", "sRGB/d4e7", True, None),
    ("sRGB/d1e7", "sRGB/d1e7", True, None),
])
def test_rederive_reason(stored, wanted, check, expected):
    reason = rec._rederive_reason(stored, wanted, check)
    if expected is None:
        assert reason is None
    else:
        assert expected in reason


# ---------------------------------------------------------------------------
# T2 - real codecs: the six-step sync sequence
# ---------------------------------------------------------------------------

def _saturated_png(path: Path):
    import numpy as np
    import imagecodecs
    x = np.linspace(0, 1, 64)
    yy, xx = np.meshgrid(x, x, indexing="ij")
    arr = np.stack([(xx * 65535).astype("uint16"),
                    (yy * 65535).astype("uint16"),
                    ((1 - xx) * (1 - yy) * 65535).astype("uint16")], axis=2)
    path.write_bytes(imagecodecs.png_encode(arr))


def _make_master(tmp_path: Path, name: str = "master.jxl") -> Path:
    """A synthetic ProPhoto-like master with the encoder's markers (copy of
    tests/test_output_icc.py's helper)."""
    import base64
    pp = tmp_path / "pp.icc"
    pp.write_bytes(rec._build_matrix_trc_icc(
        "test-prophoto", [(0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001)],
        (0.3457, 0.3585), 1.8))
    raw = tmp_path / "raw.png"
    src = tmp_path / "src.png"
    _saturated_png(raw)
    subprocess.run(["magick", str(raw), "-profile", str(pp), str(src)],
                   check=True, capture_output=True)
    master = tmp_path / name
    subprocess.run(["cjxl", str(src), str(master), "-d", "0.05",
                    "--container=1", "-x", "strip=exif", "-x", "strip=xmp"],
                   check=True, capture_output=True)
    b64 = base64.b64encode(pp.read_bytes()).decode("ascii")
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    f"-XMP-xmp:CreatorTool=Test | ICC:{b64}",
                    "-XMP-dc:Description=gen=1 | cjxl d=0.05 e=7",
                    "-XMP-dc:Relation+=jxlphoto-src:1111",
                    "-XMP-dc:Relation+=jxlphoto-srcsum:2222",
                    "-XMP-dc:Relation+=jxlphoto-depth:16", str(master)],
                   check=True, capture_output=True)
    return master


def _exif_value(path: Path, tag: str):
    r = subprocess.run(["exiftool", "-j", "-s", "-s", tag, str(path)],
                       capture_output=True, text=True, timeout=60)
    entry = json.loads(r.stdout)[0]
    return entry.get(tag.split(":")[-1].lstrip("-"))


def _relation_tokens(path: Path) -> list:
    rel = _exif_value(path, "-XMP-dc:Relation")
    if rel is None:
        return []
    return [str(t).strip() for t in (rel if isinstance(rel, list) else [rel])]


def _run_derive(tmp_path: Path, distance, extra=(), sync=True):
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--output-icc", "sRGB",
                        "--workers", "1", "--no-preflight",
                        *(["--sync"] if sync else []),
                        "--distance", str(distance), *extra],
                       capture_output=True, text=True, timeout=600,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


@real
def test_real_derivative_rederives_on_encode_change(tmp_path):
    pytest.importorskip("PIL.ImageCms")
    _make_master(tmp_path)
    out = tmp_path / rec.CONVERTED_JXL_FOLDER / "master.jxl"
    floor = rec._min_effective_distance(rec._get_cjxl_cmd() or "cjxl")

    def expected(distance):
        return ("jxlphoto-derived:sRGB"
                + rec._encode_suffix(distance, rec.CJXL_EFFORT, floor))

    # 1. D=1.0 -> the derivative records recipe + distance/effort.
    _run_derive(tmp_path, 1.0)
    assert out.exists()
    assert expected(1.0) in _relation_tokens(out)

    # 2. same D again -> SKIP, file untouched.
    before = out.stat().st_mtime_ns
    r = _run_derive(tmp_path, 1.0)
    assert "SKIP (exists)" in r.stdout, r.stdout
    assert out.stat().st_mtime_ns == before

    # 3. D=2.0 -> re-derived because the encode record changed.
    r = _run_derive(tmp_path, 2.0)
    assert out.stat().st_mtime_ns != before
    assert expected(2.0) in _relation_tokens(out)
    assert "encode settings changed (d=1 e=" in r.stdout, r.stdout

    # 4. D=3.0 with the check disabled -> kept at D=2.0, untouched.
    before = out.stat().st_mtime_ns
    r = _run_derive(tmp_path, 3.0, ("--no-rederive-on-encode-change",))
    assert out.stat().st_mtime_ns == before
    assert expected(2.0) in _relation_tokens(out)

    # 5. preview: D=3.0 would be re-derived (never SKIP).
    r = _run_derive(tmp_path, 3.0, ("--dry-run",))
    assert " DRY | DERIVE (sRGB/d3e" in r.stdout, r.stdout
    assert "SKIP (exists)" not in r.stdout, r.stdout

    # 6. a pre-suffix label (v2.6.2) is never re-derived for d/e: say so and
    #    keep the file. exiftool rewrites the file, so the reference mtime is
    #    recorded AFTER the edit.
    token = expected(2.0)
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    f"-XMP-dc:Relation-={token}",
                    "-XMP-dc:Relation+=jxlphoto-derived:sRGB", str(out)],
                   check=True, capture_output=True)
    assert "jxlphoto-derived:sRGB" in _relation_tokens(out)
    before = out.stat().st_mtime_ns
    r = _run_derive(tmp_path, 4.0)
    assert out.stat().st_mtime_ns == before
    assert "predate the distance/effort record" in r.stdout, r.stdout

    # 7. the refresh the hint suggests: --overwrite (no --sync, which would
    #    win) re-derives it — and must not tell the user to run --overwrite.
    r = _run_derive(tmp_path, 4.0, ("--overwrite",), sync=False)
    assert out.stat().st_mtime_ns != before
    assert expected(4.0) in _relation_tokens(out)
    assert "predate the distance/effort record" not in r.stdout, r.stdout


# ---------------------------------------------------------------------------
# T3 - CLI
# ---------------------------------------------------------------------------

def test_cli_rederive_flags_are_mutually_exclusive(tmp_path):
    (tmp_path / "a.jxl").write_bytes(
        b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--dry-run",
                        "--rederive-on-encode-change",
                        "--no-rederive-on-encode-change"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2, r.stdout + r.stderr
    # The mutually exclusive group refused it — not an unknown flag.
    assert "not allowed with argument" in r.stderr, r.stderr


def test_cli_rederive_flag_on_a_non_derivative_run_warns(tmp_path):
    (tmp_path / "a.jxl").write_bytes(
        b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"),
                        str(tmp_path), "--mode", "1", "--dry-run",
                        "--no-rederive-on-encode-change"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "only apply to derivatives" in r.stdout + r.stderr, r.stdout + r.stderr


# --- T4/T5/T6 (wrapper tests) appended below by the second implementer ---


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _input_feeder(answers):
    """A builtins.input double: `answers` in order, then "" forever (declines
    every later question, like a closed stdin)."""
    it = iter(answers)

    def _fake_input(*a, **k):
        try:
            return next(it)
        except StopIteration:
            return ""
    return _fake_input


# ---------------------------------------------------------------------------
# T4 - the two command builders pass the chosen policy
# ---------------------------------------------------------------------------

def _manifest_cmd(advanced):
    return _menu()._build_manifest_entry_cmd(
        script="jxl_recompressor.py",
        source=str(Path("F:/lib")), dest_path=str(Path("F:/out")), mode=2,
        origin="jxl", dest="jxl", workers=2,
        workflow={"distance": 1.0, "effort": 7, "mode_config": {}},
        advanced=advanced,
    )


def test_manifest_cmd_carries_rederive_flag():
    cmd = _manifest_cmd({"rederive_on_encode_change": True, "output_icc": "sRGB"})
    assert "--rederive-on-encode-change" in cmd
    assert "--no-rederive-on-encode-change" not in cmd

    cmd = _manifest_cmd({"rederive_on_encode_change": False,
                         "resize_mode": "long", "resize_value": 2048})
    assert "--no-rederive-on-encode-change" in cmd
    assert "--rederive-on-encode-change" not in cmd

    cmd = _manifest_cmd({"on_unknown": "convert", "output_icc": "sRGB"})
    assert "--rederive-on-encode-change" not in cmd
    assert "--no-rederive-on-encode-change" not in cmd

    # A plain recompression: the child would ignore the flag with a warning
    # on every run, so the wrapper does not pass it.
    cmd = _manifest_cmd({"rederive_on_encode_change": False})
    assert "--rederive-on-encode-change" not in cmd
    assert "--no-rederive-on-encode-change" not in cmd


@pytest.mark.parametrize("advanced,expected,absent", [
    ({"rederive_on_encode_change": True, "output_icc": "sRGB"},
     "--rederive-on-encode-change", "--no-rederive-on-encode-change"),
    ({"rederive_on_encode_change": False, "sharpen": "screen"},
     "--no-rederive-on-encode-change", "--rederive-on-encode-change"),
    ({"on_unknown": "convert", "output_icc": "sRGB"}, None, None),
    ({"rederive_on_encode_change": True}, None, None),
])
def test_direct_cmd_carries_rederive_flag(tmp_path, monkeypatch, advanced, expected, absent):
    menu = _menu()
    calls = []
    # execute_workflow spawns through _stream_child (not _run_subprocess).
    monkeypatch.setattr(menu, "_stream_child",
                        lambda cmd: calls.append([str(c) for c in cmd]) or 0)
    workflow = {
        'mode': 1, 'origin_format': 'jxl', 'dest_format': 'jxl',
        'conversion_type': 'jxl_recompress',
        'input_dir': str(tmp_path), 'workers': 2,
        'distance': 1.0, 'effort': 7,
        'advanced_options': advanced, 'mode_config': {}, 'expert_flags': '',
        'dry_run': True,
    }
    menu.execute_workflow(workflow, {})
    cmd = calls[-1]
    if expected is None:
        assert "--rederive-on-encode-change" not in cmd
        assert "--no-rederive-on-encode-change" not in cmd
    else:
        assert expected in cmd
        assert absent not in cmd


# ---------------------------------------------------------------------------
# T5 - the wrapper reads a BOOLEAN child setting (False is a real value)
# ---------------------------------------------------------------------------

def test_child_bool_setting_reads_a_child_false(monkeypatch):
    monkeypatch.setattr(rec, "REDERIVE_ON_ENCODE_CHANGE", False)
    # False read by the boolean-aware reader...
    assert wp._child_bool_setting(
        'jxl_recompressor', 'REDERIVE_ON_ENCODE_CHANGE', True) is False
    # ...while _child_setting turns it into the fallback — exactly the trap
    # _child_bool_setting exists to avoid.
    assert wp._child_setting(
        'jxl_recompressor', 'REDERIVE_ON_ENCODE_CHANGE', True) is True


def test_child_bool_setting_fallbacks():
    # A child that cannot be imported, or that lacks the setting: fallback.
    assert wp._child_bool_setting(
        'no_such_module_xyz', 'REDERIVE_ON_ENCODE_CHANGE', True) is True
    assert wp._child_bool_setting(
        'no_such_module_xyz', 'REDERIVE_ON_ENCODE_CHANGE', False) is False
    assert wp._child_bool_setting(
        'jxl_recompressor', 'NO_SUCH_SETTING', False) is False


# ---------------------------------------------------------------------------
# T6 - the wizard asks the question on the recompression branch (plain input)
# ---------------------------------------------------------------------------

def _recompress_workflow():
    return {"origin_format": "jxl", "dest_format": "jxl",
            "conversion_type": "jxl_recompress", "overwrite_mode": "2"}


def test_wizard_answers_no_to_rederive(monkeypatch):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", _input_feeder(["y", "", "", "n"]))
    menu = _menu()
    workflow = _recompress_workflow()
    menu._wizard_parameters_advanced(workflow, {})
    adv = workflow["advanced_options"]
    assert adv.get("on_unknown") == "convert"
    assert adv.get("jbrd_policy") == "copy"
    assert adv["rederive_on_encode_change"] is False


def test_wizard_rederive_default_comes_from_the_child(monkeypatch):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", _input_feeder(["y", "", "", ""]))
    monkeypatch.setattr(rec, "REDERIVE_ON_ENCODE_CHANGE", False)
    menu = _menu()
    workflow = _recompress_workflow()
    menu._wizard_parameters_advanced(workflow, {})
    # Empty answers to the three recompressor questions keep their defaults,
    # and the new question's default came from the child's setting: False.
    assert workflow["advanced_options"]["rederive_on_encode_change"] is False
