#!/usr/bin/env python3
"""The recompressor skips up-to-date outputs BEFORE planning reads them.

convert_one SKIPs an existing, up-to-date output before it looks at the
item's action, so the encode record (exiftool) and the jbrd box read during
planning could never change the outcome of such an item. On a sync where
almost everything is up to date that was nearly the whole planning time
(minutes on a hard disk). _mark_up_to_date_items now decides those items from
two stats; only the rest is planned.

Never on a run that deletes, with --overwrite or in place; a derivative run
reads the existing output's jxlphoto-derived label first and plans fully
anything that is not its own current derivative.

Every fixture lives in a hostile folder (Japanese, [ ], a space, %).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import jxl_recompressor as rec  # noqa: E402

real = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("cjxl", "djxl", "exiftool")),
    reason="needs cjxl, djxl and exiftool")

HOSTILE = "250118_吾妻山公園_菜の花 [FINAL] 5%"
NAMES = ("DSC_0001 (2).jxl", "写真 [b].jxl")


def _make_sources(tmp_path: Path) -> Path:
    from PIL import Image
    src = tmp_path / HOSTILE
    src.mkdir()
    for i, name in enumerate(NAMES):
        png = tmp_path / f"p{i}.png"
        img = np.zeros((48, 64, 3), dtype=np.uint8)
        img[..., 0] = np.arange(64, dtype=np.uint8)[None, :] * 3 + i * 40
        img[..., 1] = np.arange(48, dtype=np.uint8)[:, None] * 4
        img[..., 2] = 90
        Image.fromarray(img).save(png)
        r = subprocess.run(["cjxl", str(png), str(src / name), "-d", "0.3", "-e", "1",
                            "--container=1"],
                           capture_output=True, timeout=60)
        assert r.returncode == 0, r.stderr
    return src


def _run_sub(src: Path, *extra):
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"), str(src),
                        "--mode", "1", "--distance", "2", "--effort", "3",
                        "--workers", "1", "--no-preflight", "--on-unknown", "convert",
                        *extra],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def _make_newer(src_file: Path, out_file: Path):
    t = out_file.stat().st_mtime + 10
    os.utime(src_file, (t, t))


def _spy(monkeypatch):
    """Record which sources planning reads (encode record + jbrd box), and the
    summary the run emits."""
    seen = {"records": [], "jbrd": [], "summary": {}}
    real_records = rec._read_encode_params_batch
    real_jbrd = rec.has_jbrd_box

    def records(paths):
        seen["records"].extend(Path(p).name for p in paths)
        return real_records(paths)

    def jbrd(path):
        seen["jbrd"].append(Path(path).name)
        return real_jbrd(path)

    monkeypatch.setattr(rec, "_read_encode_params_batch", records)
    monkeypatch.setattr(rec, "has_jbrd_box", jbrd)
    monkeypatch.setattr(rec, "emit_summary_json",
                        lambda *a, **k: seen["summary"].update(k))
    return seen


def _run_main(monkeypatch, src: Path, *extra):
    monkeypatch.setattr(sys, "argv", [
        "jxl_recompressor.py", str(src), "--mode", "1", "--distance", "2",
        "--effort", "3", "--workers", "1", "--no-preflight",
        "--on-unknown", "convert", "--summary-json", *extra])
    with pytest.raises(SystemExit) as e:
        rec.main()
    return e.value.code


@real
def test_sync_plans_only_what_is_not_up_to_date(tmp_path, monkeypatch, caplog):
    src = _make_sources(tmp_path)
    out_dir = src / rec.CONVERTED_JXL_FOLDER
    _run_sub(src)
    outs = [out_dir / n for n in NAMES]
    assert all(o.exists() for o in outs)

    # The first source changes; the second output stays up to date.
    _make_newer(src / NAMES[0], outs[0])
    kept_mtime = outs[1].stat().st_mtime_ns
    redone_mtime = outs[0].stat().st_mtime_ns

    seen = _spy(monkeypatch)
    caplog.set_level("INFO", logger=rec.logger.name)
    assert _run_main(monkeypatch, src) in (0, None)

    assert seen["records"] == [NAMES[0]], seen
    assert set(seen["jbrd"]) == {NAMES[0]}, seen    # conversion reads it again
    text = caplog.text
    assert "1 file(s) already up to date at the destination" in text, text
    assert "Planning 1 file(s)" in text, text
    assert f"SKIP (exists) | {NAMES[1]}" in text, text
    assert outs[1].stat().st_mtime_ns == kept_mtime
    assert outs[0].stat().st_mtime_ns != redone_mtime
    assert seen["summary"].get("skipped") == 1, seen["summary"]
    assert seen["summary"].get("ok") == 1, seen["summary"]
    assert seen["summary"].get("errors") == 0, seen["summary"]


@real
def test_everything_up_to_date_reads_nothing_and_previews_skips(tmp_path):
    src = _make_sources(tmp_path)
    _run_sub(src)
    r = _run_sub(src, "--dry-run")
    assert "2 file(s) already up to date at the destination" in r.stdout, r.stdout
    assert "Planning " not in r.stdout, r.stdout
    for n in NAMES:
        assert f" DRY | SKIP (exists) | {n}" in r.stdout, r.stdout
    r = _run_sub(src)
    assert "Planning " not in r.stdout, r.stdout
    assert "0 recompressed" in r.stdout and "2 skipped" in r.stdout, r.stdout


@real
@pytest.mark.parametrize("extra", [
    ("--delete-source", "--dry-run"),
    ("--overwrite", "--dry-run"),
])
def test_runs_that_delete_or_overwrite_plan_everything(tmp_path, monkeypatch, extra):
    src = _make_sources(tmp_path)
    _run_sub(src)
    seen = _spy(monkeypatch)
    assert _run_main(monkeypatch, src, *extra) in (0, None)
    assert sorted(seen["records"]) == sorted(NAMES), seen
    assert set(seen["jbrd"]) == set(NAMES), seen


@real
def test_in_place_items_are_always_planned(tmp_path, monkeypatch):
    # Mode 8: the output IS the source, which _would_skip alone would call
    # up to date (same mtime) — an in-place item is never a skip.
    src = _make_sources(tmp_path)
    seen = _spy(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "jxl_recompressor.py", str(src), "--mode", "8", "--distance", "2",
        "--effort", "3", "--workers", "1", "--no-preflight",
        "--on-unknown", "convert", "--summary-json", "--dry-run"])
    with pytest.raises(SystemExit) as e:
        rec.main()
    assert e.value.code in (0, None)
    assert sorted(seen["records"]) == sorted(NAMES), seen
    assert seen["summary"].get("ok") == 2, seen["summary"]


@real
def test_up_to_date_items_never_reach_the_batch_prompt(tmp_path, monkeypatch):
    src = _make_sources(tmp_path)
    _run_sub(src)
    _make_newer(src / NAMES[0], src / rec.CONVERTED_JXL_FOLDER / NAMES[0])
    asked = []
    monkeypatch.setattr(rec, "_ask_batch_resolution",
                        lambda asks: asked.extend(it["src"].name for it in asks))
    _spy(monkeypatch)
    _run_main(monkeypatch, src, "--on-unknown", "ask", "--dry-run")
    assert asked == [], asked     # a dry run never prompts: the list is empty
    monkeypatch.setattr(sys, "stdin", open(os.devnull))
    _run_main(monkeypatch, src, "--on-unknown", "ask")
    assert asked == [NAMES[0]], asked


def _run_derive_sub(src: Path, pct, *extra):
    return _run_sub(src, "--resize-percent", str(pct), "--on-downgrade", "convert",
                    *extra)


@real
def test_derivative_sync_skips_its_own_current_derivatives_unread(tmp_path, monkeypatch,
                                                                  caplog):
    src = _make_sources(tmp_path)
    out_dir = src / rec.CONVERTED_JXL_FOLDER
    _run_derive_sub(src, 50)
    outs = [out_dir / n for n in NAMES]
    assert all(o.exists() for o in outs)
    before = [o.stat().st_mtime_ns for o in outs]

    # Same recipe: nothing is read from the sources, nothing is rewritten.
    seen = _spy(monkeypatch)
    caplog.set_level("INFO", logger=rec.logger.name)
    assert _run_main(monkeypatch, src, "--resize-percent", "50",
                     "--on-downgrade", "convert") in (0, None)
    assert seen["records"] == [] and seen["jbrd"] == [], seen
    assert "2 file(s) already up to date at the destination" in caplog.text
    assert [o.stat().st_mtime_ns for o in outs] == before
    assert seen["summary"].get("skipped") == 2, seen["summary"]

    # Another recipe: the label differs, so both go through the full plan and
    # are re-derived, exactly as before the pre-filter.
    r = _run_derive_sub(src, 25)
    assert "derivative recipe changed" in r.stdout, r.stdout
    assert "Planning 2 file(s)" in r.stdout, r.stdout
    assert all(o.stat().st_mtime_ns != b for o, b in zip(outs, before))


@real
def test_derivative_sync_still_refuses_a_foreign_file_at_the_destination(tmp_path):
    # An up-to-date file at the destination that is NOT a derivative (here a
    # plain recompression) must keep its refusal: the pre-filter only skips
    # this run's own derivatives.
    src = _make_sources(tmp_path)
    _run_sub(src)
    r = subprocess.run([sys.executable, str(REPO / "jxl_recompressor.py"), str(src),
                        "--mode", "1", "--distance", "2", "--effort", "3",
                        "--workers", "1", "--no-preflight", "--on-unknown", "convert",
                        "--resize-percent", "50", "--on-downgrade", "convert"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, stdin=subprocess.DEVNULL)
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.count("REFUSED |") == 2, r.stdout
    assert "already up to date at the destination" not in r.stdout, r.stdout


def test_mark_up_to_date_items_respects_the_gates(tmp_path, monkeypatch):
    a, b = tmp_path / "a.jxl", tmp_path / "out.jxl"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))

    def items():
        return [{"src": a, "final": b, "in_place": False, "action": None},
                {"src": a, "final": a, "in_place": True, "action": None}]

    monkeypatch.setattr(rec, "DERIVATIVE", False)
    monkeypatch.setattr(rec, "OVERWRITE", "smart")
    monkeypatch.setattr(rec, "DELETE_SKIPPED", False)
    monkeypatch.setattr(rec, "DELETE_SOURCE", False)
    its = items()
    assert rec._mark_up_to_date_items(its) == (1, 0, 0)
    assert [it["action"] for it in its] == ["skip_uptodate", None]
    assert rec._plan_item_would_skip(its[0])

    for name, value in (("DELETE_SOURCE", True), ("DELETE_SKIPPED", True),
                        ("OVERWRITE", True)):
        monkeypatch.setattr(rec, name, value)
        its = items()
        assert rec._mark_up_to_date_items(its) == (0, 0, 0), name
        assert all(it["action"] is None for it in its)
        monkeypatch.setattr(rec, name, {"OVERWRITE": "smart"}.get(name, False))

    # A newer source is not up to date.
    os.utime(a, (3000, 3000))
    its = items()
    assert rec._mark_up_to_date_items(its) == (0, 0, 0)
