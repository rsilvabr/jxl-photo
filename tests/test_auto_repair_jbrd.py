#!/usr/bin/env python3
"""--auto-repair-jbrd + the reconstruction-failure hint (v2.1.0).

The damage: v2.0.0-v2.0.3 wrote XMP provenance markers into jbrd containers,
which makes djxl --reconstruct_jpeg fail for source JPEGs that already had
XMP. This file pins the answers:

  * a failed reconstruction names the remedies (--repair-jbrd / the flag)
  * --auto-repair-jbrd decodes from a repaired COPY — the JXL is untouched
  * the delete gate never deletes a source decoded from an auto-repaired copy
  * the wrapper asks in Step 6A (JXL->JPEG only) and passes the flag through
  * the wrapper's menu option 8 launches --repair-jbrd (audit by default)

The real-codec tests are skipped without cjxl/djxl/exiftool — the damage only
exists with the real tools.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_jpeg_transcoder as tr
import jxl_photo as wp

REPO = Path(__file__).resolve().parent.parent

_HAVE_TOOLS = all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool"))
real = pytest.mark.skipif(not _HAVE_TOOLS, reason="needs cjxl, djxl and exiftool on PATH")


def _menu(tmp_path, monkeypatch):
    """A menu on a throwaway config — never the user's real one."""
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


def _captured_children(monkeypatch):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (calls.append(list(map(str, cmd))), 0)[1])
    return calls


def _jxl2jpeg_workflow(tmp_path, advanced):
    return {
        'mode': 1, 'advanced_options': advanced, 'dry_run': True,
        'origin_format': 'jxl', 'dest_format': 'jpeg',
        'input_dir': str(tmp_path), 'workers': 2, 'compression': 'zip',
        'bit_depth': 16, 'mode_config': {}, 'expert_flags': '',
        'quality': 95, 'effort': 7, 'use_ram': True,
    }


def test_wrapper_passes_auto_repair_flag(tmp_path, monkeypatch):
    menu = _menu(tmp_path, monkeypatch)
    calls = _captured_children(monkeypatch)
    menu.execute_workflow(_jxl2jpeg_workflow(tmp_path, {'auto_repair_jbrd': True}), {})
    assert '--auto-repair-jbrd' in calls[0], calls[0]


def test_wrapper_omits_auto_repair_by_default(tmp_path, monkeypatch):
    menu = _menu(tmp_path, monkeypatch)
    calls = _captured_children(monkeypatch)
    menu.execute_workflow(_jxl2jpeg_workflow(tmp_path, {'overwrite': False, 'sync': True}), {})
    assert '--auto-repair-jbrd' not in calls[0], calls[0]


def test_wrapper_menu8_audits_by_default(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    menu = _menu(tmp_path, monkeypatch)
    calls = _captured_children(monkeypatch)
    src = tmp_path / "archive"
    src.mkdir()
    answers = iter([str(src), ""])  # folder, [Y] audit
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    menu.repair_jbrd_flow({'djxl': True, 'exiftool': True})
    assert calls and '--repair-jbrd' in calls[0] and '--dry-run' in calls[0], calls


def test_wrapper_menu8_repair_writes_on_request(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    menu = _menu(tmp_path, monkeypatch)
    calls = _captured_children(monkeypatch)
    src = tmp_path / "archive"
    src.mkdir()
    answers = iter([str(src), "n"])  # folder, repair for real
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    menu.repair_jbrd_flow({'djxl': True, 'exiftool': True})
    assert calls and '--repair-jbrd' in calls[0] and '--dry-run' not in calls[0], calls


# ---------------------------------------------------------------------------
# Real codecs: the actual v2.0.0-v2.0.3 damage shape
# ---------------------------------------------------------------------------

def _damaged_jbrd(tmp_path: Path) -> Path:
    """A jbrd JXL the way v2.0.0-v2.0.3 left it: source JPEG with XMP,
    toolkit markers written into the container, reconstruction broken."""
    from PIL import Image
    import numpy as np
    jpg = tmp_path / "photo.jpg"
    arr = (np.random.default_rng(1).random((96, 128, 3)) * 255).astype("uint8")
    Image.fromarray(arr).save(jpg, quality=90)
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-dc:Description=caption", "-Make=NIKON", str(jpg)],
                   check=True, capture_output=True)
    jxl = tmp_path / "photo.jxl"
    subprocess.run(["cjxl", str(jpg), str(jxl), "--lossless_jpeg=1"],
                   check=True, capture_output=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original",
                    "-XMP-dc:Relation+=jxlphoto-src:0123456789abcdef",
                    "-XMP-dc:Relation+=jxlphoto-srcsum:fedcba9876543210",
                    str(jxl)], check=True, capture_output=True)
    assert tr._jxl_reconstruct_md5(jxl) is None, "fixture no longer reproduces the damage"
    return jxl


def _decode_dir_of(jxl: Path, *extra):
    work = jxl.parent / f"work_{len(list(jxl.parent.glob('work_*')))}"
    work.mkdir()
    (work / jxl.name).write_bytes(jxl.read_bytes())
    r = subprocess.run(
        [sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), str(work),
         "--mode", "1", *extra],
        capture_output=True, text=True, timeout=300)
    return work, r


@real
def test_real_failed_reconstruction_names_the_remedies(tmp_path):
    jxl = _damaged_jbrd(tmp_path)
    work, r = _decode_dir_of(jxl)
    assert not list(work.rglob("*.jpg")), "the broken jbrd decoded anyway"
    out = r.stdout + r.stderr
    assert "--repair-jbrd" in out and "--auto-repair-jbrd" in out, out[-2000:]


@real
def test_real_auto_repair_decodes_from_a_copy_and_never_touches_the_jxl(tmp_path):
    jxl = _damaged_jbrd(tmp_path)
    work, r = _decode_dir_of(jxl, "--auto-repair-jbrd")
    produced = list(work.rglob("*.jpg"))
    assert produced, f"no JPEG decoded\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
    # The JXL on disk is byte-for-byte untouched...
    assert (work / jxl.name).read_bytes() == jxl.read_bytes()
    # ...and the recovered JPEG carries the same pixels as the original.
    from PIL import Image
    import numpy as np
    orig = np.array(Image.open(tmp_path / "photo.jpg").convert("RGB"))
    reco = np.array(Image.open(produced[0]).convert("RGB"))
    assert orig.shape == reco.shape and np.array_equal(orig, reco)


@real
def test_real_delete_gate_keeps_an_auto_repaired_source(tmp_path):
    jxl = _damaged_jbrd(tmp_path)
    work, r = _decode_dir_of(jxl, "--auto-repair-jbrd",
                             "--delete-source", "--delete-confirm-off")
    assert list(work.rglob("*.jpg")), f"no JPEG decoded\n{r.stdout[-2000:]}"
    assert (work / jxl.name).exists(), "auto-repaired decode deleted the still-broken JXL"
