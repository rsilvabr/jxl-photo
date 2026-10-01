#!/usr/bin/env python3
"""Round 43 regression tests for jxl_recompressor.py (261001 audit).

  R-1  overwriting a pre-existing output whose provenance markers name a
       DIFFERENT origin must log a loud warning (warn only, never block).
  R-2  run-scoped, CLI-mutated globals reset unconditionally at main() entry,
       so a second in-process run cannot inherit armed state.
  R-3  `_counter["done"]` is zeroed at main() entry.
  R-4  a multi-page (jxlphoto-mpg) group replaced IN PLACE (mode 8) is
       all-or-nothing: a failed page vetoes the replacement of its siblings.

They import only the child script, so they fail against an extracted HEAD copy
(pre-fix proof) as well as passing here.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_recompressor as rec

REPO = Path(__file__).resolve().parent.parent

_JXL_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n"


def _jxl_stub(path: Path, payload: bytes = b"\x00" * 32) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_JXL_SIG + payload)
    return path


class _FakeLogger:
    def __init__(self):
        self.infos, self.warnings, self.errors = [], [], []

    def info(self, m):
        self.infos.append(str(m))

    def warning(self, m):
        self.warnings.append(str(m))

    def error(self, m):
        self.errors.append(str(m))

    def debug(self, m):
        pass


@pytest.fixture(autouse=True)
def _reset_rec_globals():
    """Pin every run-scoped global this file touches, before AND after each
    test: main() mutates them through a `global` statement, and a leaked
    delete arm would poison the rest of the session's tests."""
    def _clean():
        rec.OVERWRITE = "smart"
        rec.DELETE_SOURCE = False
        rec.DELETE_CONFIRM = True
        rec.DELETE_SKIPPED = False
        rec.VERIFY_ROUNDTRIP = False
        rec.KEEP_SMALLER = True
        rec.ON_DOWNGRADE = "ask"
        rec.ON_REGENERATION = "ask"
        rec.ON_UNKNOWN = "convert"
        rec.JBRD_POLICY = "copy"
        rec.ENCODE_TAG_MODE = "xmp"
        rec.CJXL_DISTANCE = 1.0
        rec.CJXL_EFFORT = 7
        rec.CJXL_BUFFERING = None
        rec.PROVENANCE_CHECK = "path"
        rec.TEMP2_DIR = None
        rec._counter["done"] = 0
        rec._counter["total"] = 0
        for k in rec._delete_stats:
            rec._delete_stats[k] = 0
    _clean()
    yield
    _clean()


def _patch_main_plumbing(monkeypatch, captured):
    """Stub everything main() needs that is not the behaviour under test."""
    monkeypatch.setattr(rec, "setup_logger", lambda: None)
    fake = _FakeLogger()
    monkeypatch.setattr(rec, "logger", fake)
    monkeypatch.setattr(rec, "emit_summary_json", lambda *a, **k: None)
    monkeypatch.setattr(rec, "_read_encode_params_batch",
                        lambda paths: {str(p): {"desc": "", "software": "",
                                                "params": None, "gen": 0}
                                       for p in paths})
    monkeypatch.setattr(rec, "_read_mpg_markers", lambda paths: ({}, True))
    monkeypatch.setattr(rec, "_get_cjxl_cmd", lambda: "cjxl")
    monkeypatch.setattr(rec, "_tool_version", lambda exe: (0, 12, 0))
    monkeypatch.setattr(shutil, "which", lambda c: "C:/tools/x")

    def fake_pg(items, workers):
        captured["items"] = [dict(it) for it in items]
        return ({str(it["src"]): ("skipped", str(it["final"])) for it in items},
                set())

    monkeypatch.setattr(rec, "process_group", fake_pg)
    return fake


# ===========================================================================
# R-1 — warn when overwriting an output whose provenance names another origin
# ===========================================================================

def test_r1_warns_on_a_foreign_output(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src, b"this photo")
    final = tmp_path / "out" / "a.jxl"
    _jxl_stub(final, b"someone else's archive")

    fake = _FakeLogger()
    monkeypatch.setattr(rec, "logger", fake)
    monkeypatch.setattr(rec, "_would_skip", lambda s, f: False)
    monkeypatch.setattr(rec, "PROVENANCE_CHECK", "path")
    monkeypatch.setattr(rec, "_read_source_markers_batch",
                        lambda paths: {
                            str(final): {"src": "other-photo", "srcsum": "o"},
                            str(src): {"src": "this-photo", "srcsum": "s"},
                        })

    rec._warn_foreign_overwrite([{"src": src, "final": final,
                                  "action": "convert", "in_place": False}])
    assert any("different origin" in w for w in fake.warnings), fake.warnings


def test_r1_no_warning_when_markers_match(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "a.jxl"
    _jxl_stub(final)

    fake = _FakeLogger()
    monkeypatch.setattr(rec, "logger", fake)
    monkeypatch.setattr(rec, "_would_skip", lambda s, f: False)
    monkeypatch.setattr(rec, "_read_source_markers_batch",
                        lambda paths: {str(p): {"src": "same", "srcsum": "same"}
                                       for p in paths})

    rec._warn_foreign_overwrite([{"src": src, "final": final,
                                  "action": "convert", "in_place": False}])
    assert not fake.warnings, fake.warnings


def test_r1_no_warning_when_output_is_markerless(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "a.jxl"
    _jxl_stub(final)

    fake = _FakeLogger()
    monkeypatch.setattr(rec, "logger", fake)
    monkeypatch.setattr(rec, "_would_skip", lambda s, f: False)
    monkeypatch.setattr(rec, "_read_source_markers_batch",
                        lambda paths: {str(p): {"src": None, "srcsum": None}
                                       for p in paths})

    rec._warn_foreign_overwrite([{"src": src, "final": final,
                                  "action": "convert", "in_place": False}])
    assert not fake.warnings, fake.warnings


def test_r1_main_warns_and_still_proceeds(tmp_path, monkeypatch):
    """End to end: the warning is logged and the run reaches its summary."""
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    src = _jxl_stub(src_dir / "a.jxl", b"this photo")
    final = _jxl_stub(src_dir / "recompressed_jxl" / "a.jxl", b"other archive")
    # src newer than the existing output: smart sync re-encodes (overwrites).
    stamp = final.stat().st_mtime + 100
    os.utime(src, (stamp, stamp))

    captured = {}
    fake = _patch_main_plumbing(monkeypatch, captured)
    monkeypatch.setattr(rec, "_read_source_markers_batch",
                        lambda paths: {
                            str(p): ({"src": "other-photo", "srcsum": "o"}
                                     if "recompressed_jxl" in str(p)
                                     else {"src": "this-photo", "srcsum": "s"})
                            for p in paths})
    monkeypatch.setattr(sys, "argv",
                        ["jxl_recompressor.py", str(src_dir), "--mode", "1",
                         "--on-unknown", "convert", "--no-preflight"])
    with pytest.raises(SystemExit) as ei:
        rec.main()
    assert ei.value.code == 0, ei.value.code
    assert any("different origin" in w for w in fake.warnings), fake.warnings
    assert captured["items"], "the run did not reach its work plan"


# ===========================================================================
# R-2 / R-3 — run-scoped globals and the progress counter reset per run
# ===========================================================================

def test_r2_second_main_does_not_inherit_armed_state(tmp_path, monkeypatch):
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    _jxl_stub(src_dir / "a.jxl")
    staging = tmp_path / "stage"
    staging.mkdir()

    captured = {}
    _patch_main_plumbing(monkeypatch, captured)

    # Run 1 — arm deletion, disable the confirmation and set staging.
    monkeypatch.setattr(sys, "argv",
                        ["jxl_recompressor.py", str(src_dir), "--mode", "1",
                         "--on-unknown", "convert", "--delete-source",
                         "--delete-confirm-off", "--staging", str(staging),
                         "--overwrite", "--no-preflight"])
    with pytest.raises(SystemExit) as first:
        rec.main()
    assert first.value.code == 0
    assert rec.DELETE_SOURCE is True and rec.DELETE_CONFIRM is False
    assert rec.TEMP2_DIR == Path(staging) and rec.OVERWRITE is True

    # Run 2 — plain. Every flag above must fall back to its script default.
    monkeypatch.setattr(sys, "argv",
                        ["jxl_recompressor.py", str(src_dir), "--mode", "1",
                         "--on-unknown", "convert", "--no-preflight"])
    with pytest.raises(SystemExit) as second:
        rec.main()
    assert second.value.code == 0
    assert rec.DELETE_SOURCE is False, "second run inherited --delete-source"
    assert rec.DELETE_CONFIRM is True, "second run inherited --delete-confirm-off"
    assert rec.DELETE_SKIPPED is False
    assert rec.OVERWRITE == "smart", "second run inherited --overwrite"
    assert rec.TEMP2_DIR is None, "second run inherited --staging"


def test_r3_progress_counter_is_zeroed_per_run(tmp_path, monkeypatch):
    src_dir = tmp_path / "in"
    src_dir.mkdir()
    _jxl_stub(src_dir / "a.jxl")

    captured = {}

    def counting_pg(items, workers):
        # Simulate one file's progress; the counter must start from 0 this run.
        rec.next_count()
        return ({str(it["src"]): ("skipped", str(it["final"])) for it in items},
                set())

    _patch_main_plumbing(monkeypatch, captured)
    monkeypatch.setattr(rec, "process_group", counting_pg)

    rec._counter["done"] = 7          # a previous run's leftover
    monkeypatch.setattr(sys, "argv",
                        ["jxl_recompressor.py", str(src_dir), "--mode", "1",
                         "--on-unknown", "convert", "--no-preflight"])
    with pytest.raises(SystemExit):
        rec.main()
    assert rec._counter["done"] == 1, "the progress counter was not reset"


# ===========================================================================
# R-4 — multi-page groups replaced in place are all-or-nothing
# ===========================================================================

def _in_place_group(tmp_path, monkeypatch, second_ok):
    originals = {}
    items = []
    srcs = []
    for name in ("p0.jxl", "p1.jxl"):
        src = _jxl_stub(tmp_path / name, name.encode())
        originals[src] = src.read_bytes()
        write = src.parent / f"{'ab' * 16}_{src.stem}.tmp"
        items.append({"src": src, "final": src, "write": write,
                      "action": "convert", "in_place": True,
                      "desc": "", "software": "", "src_d": 1.0})
        srcs.append(src)

    rec.setup_logger()
    monkeypatch.setattr(rec, "TEMP2_DIR", None)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers",
                        lambda paths: ({str(p): "group-1" for p in paths}, True))

    def fake_convert(src, w, f, action, in_place, desc, software, src_d):
        if Path(src).name == "p1.jxl" and not second_ok:
            return (str(src), "error", str(f))
        Path(w).write_bytes(b"RECOMPRESSED")
        return (str(src), "ok", str(f))

    monkeypatch.setattr(rec, "convert_one", fake_convert)
    return items, srcs, originals


def test_r4_failed_page_vetoes_the_in_place_group(tmp_path, monkeypatch):
    items, srcs, originals = _in_place_group(tmp_path, monkeypatch, second_ok=False)
    rec.process_group(items, 1)
    assert srcs[0].read_bytes() == originals[srcs[0]], \
        "page 0 was replaced while its sibling page failed its gate"
    assert srcs[1].read_bytes() == originals[srcs[1]]


def test_r4_complete_group_is_replaced(tmp_path, monkeypatch):
    items, srcs, _originals = _in_place_group(tmp_path, monkeypatch, second_ok=True)
    _results, promoted = rec.process_group(items, 1)
    assert srcs[0].read_bytes() == b"RECOMPRESSED"
    assert srcs[1].read_bytes() == b"RECOMPRESSED"
    assert str(srcs[0]) in promoted and str(srcs[1]) in promoted


def _in_place_singles(tmp_path, monkeypatch, names, mpg_of=None, on_convert=None):
    items, srcs = [], []
    for name in names:
        src = _jxl_stub(tmp_path / name, name.encode())
        write = src.parent / f"{'cd' * 16}_{src.stem}.tmp"
        items.append({"src": src, "final": src, "write": write,
                      "action": "convert", "in_place": True,
                      "desc": "", "software": "", "src_d": 1.0})
        srcs.append(src)
    rec.setup_logger()
    monkeypatch.setattr(rec, "TEMP2_DIR", None)
    monkeypatch.setattr(rec, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(rec, "_read_mpg_markers",
                        lambda paths: (dict(mpg_of or {}), True))

    def fake_convert(src, w, f, action, in_place, desc, software, src_d):
        Path(w).write_bytes(b"RECOMPRESSED")
        if on_convert:
            on_convert(Path(src))
        return (str(src), "ok", str(f))

    monkeypatch.setattr(rec, "convert_one", fake_convert)
    return items, srcs


def test_r4_single_file_is_replaced_the_moment_it_settles(tmp_path, monkeypatch):
    """Only multi-page pages wait for their group. A plain in-place file is
    replaced as soon as it settles — deferring every file to the end of the
    run kept a whole tree's re-encodes on disk at once."""
    seen = {}

    def on_convert(src):
        if src.name == "b.jxl":
            # a.jxl settled before b.jxl was even converted (1 worker).
            seen["a_when_b_converts"] = (tmp_path / "a.jxl").read_bytes()

    items, srcs = _in_place_singles(tmp_path, monkeypatch, ["a.jxl", "b.jxl"],
                                    on_convert=on_convert)
    rec.process_group(items, 1)
    assert seen["a_when_b_converts"] == b"RECOMPRESSED", \
        "a.jxl waited for the rest of the run instead of being replaced"
    assert srcs[1].read_bytes() == b"RECOMPRESSED"


def test_r4_interrupt_removes_the_waiting_group_temps(tmp_path, monkeypatch):
    """A Ctrl+C with group pages still waiting leaves the originals intact
    and NO verified .tmp orphan behind."""
    names = ["p0.jxl", "p1.jxl", "z.jxl"]

    def on_convert(src):
        if src.name == "z.jxl":
            raise KeyboardInterrupt

    items, srcs = _in_place_singles(
        tmp_path, monkeypatch, names,
        mpg_of={str(tmp_path / "p0.jxl"): "g", str(tmp_path / "p1.jxl"): "g"},
        on_convert=on_convert)
    original_p0 = srcs[0].read_bytes()
    # p1 never settles: z.jxl (submitted before it completes) interrupts.
    items = [items[0], items[2], items[1]]
    with pytest.raises(KeyboardInterrupt):
        rec.process_group(items, 1)
    assert srcs[0].read_bytes() == original_p0, "a group page was replaced alone"
    # p0's verified re-encode was WAITING for its group: an orphan now.
    # (z's own temp is the in-flight file's, which convert_one owns.)
    assert not items[0]["write"].exists(), "the waiting group temp was left behind"


def test_r4_vetoed_group_leaves_no_temp(tmp_path, monkeypatch):
    items, srcs, originals = _in_place_group(tmp_path, monkeypatch, second_ok=False)
    rec.process_group(items, 1)
    assert not list(tmp_path.glob("*.tmp"))


def test_r4_plan_holds_a_run_when_a_sibling_is_policy_skipped(tmp_path, monkeypatch):
    a = {"src": tmp_path / "p0.jxl", "final": tmp_path / "p0.jxl",
         "action": "convert", "in_place": True, "reason": ""}
    b = {"src": tmp_path / "p1.jxl", "final": tmp_path / "p1.jxl",
         "action": "skip", "in_place": True, "reason": "downgrade"}
    _jxl_stub(a["src"])
    _jxl_stub(b["src"])
    rec.setup_logger()
    fake = _FakeLogger()
    monkeypatch.setattr(rec, "logger", fake)
    monkeypatch.setattr(rec, "_read_mpg_markers",
                        lambda paths: ({str(p): "group-1" for p in paths}, True))
    rec._hold_incomplete_in_place_groups([a, b])
    assert a["action"] == "skip", "a runnable page was left to replace alone"
    assert any("GROUP HELD IN PLACE" in w for w in fake.warnings), fake.warnings
