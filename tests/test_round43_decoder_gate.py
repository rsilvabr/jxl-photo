#!/usr/bin/env python3
"""Round 43 — decoder fixes from the 261001 audit.

  D-2  --delete-skipped must certify a skipped source by the existing output's
        provenance MATCH (jxlphoto-src/srcsum naming THESE sources), not by
        marker presence (the recompressor's #419 fix, never ported here); the
        dry-run preview must use the same predicate.
  D-1  every run-scoped global main() assigns must be restored between runs
        (the DELETE_SOURCE leak that made tests order-dependent).
  D-3  #432 port: the HHMM delete confirmation is charged only when the plan
        can actually delete something (a no-TTY re-run of an already-archived
        folder must not exit 3 forever).
  D-5  _decoded_in_original_space returning None must fail CLOSED on the
        paste-the-original-ICC fallback (linear-sRGB trap on lossy ICC-blob
        files) instead of logging DEBUG and pasting.
  D-6  _delete_stats["kept"] counts per SOURCE everywhere (the summary says
        "file(s)"); the per-group branches mistook a group for a file.

External tools (djxl/exiftool) are stubbed/mocked the way the neighbouring
decoder suites do: the provenance layer under test is the predicate plumbing
around _read_source_markers_batch and its callers.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_tiff_decoder as dec

# The script's own settings, captured before any test runs: "run 2 falls back
# to the defaults" must compare against what the user wrote at the top of the
# script, never against a literal copy of the shipped default.
_SCRIPT_SETTINGS = {name: getattr(dec, name) for name in (
    "DELETE_SOURCE", "DELETE_SKIPPED", "DELETE_CONFIRM", "ADD_JPEG_PREVIEW",
    "USE_MATRIX_MODE")}

pytestmark = pytest.mark.usefixtures("leave_globals_clean")

_JXL_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n"

# The run-scoped globals main() can touch (fix 6's own enumeration is pinned
# indirectly here: the fixture restores whatever an in-process main() armed).
_RUN_FLAGS = ("DELETE_SOURCE", "DELETE_SKIPPED", "DELETE_CONFIRM",
              "ALLOW_INCOMPLETE_GROUPS", "USE_MATRIX_MODE", "FORCE_BASIC_MODE",
              "FORCE_NONE_MODE", "CLEANUP_XMP_ICC_MARKER", "ADD_JPEG_PREVIEW",
              "RECONSTRUCT_MULTIPAGE", "THUMBNAIL_HANDLING", "THUMBNAIL_SUFFIX",
              "DEPTH_POLICY", "PROVENANCE_CHECK", "EXPORT_MARKER",
              "EXPORT_JXL_SUBFOLDER", "TEMP2_DIR", "EXCLUDE_FOLDERS",
              "OVERWRITE", "DJXL_OUTPUT_DEPTH", "TIFF_COMPRESSION")


@pytest.fixture
def leave_globals_clean():
    """An in-process main() call arms module globals ONE-WAY; restore them so
    this file leaks nothing into the rest of the suite."""
    saved = {n: getattr(dec, n) for n in _RUN_FLAGS}
    yield
    prev = getattr(dec, "_prev_run_globals", None)
    if prev:
        prev.clear()
    for n, v in saved.items():
        setattr(dec, n, v)


def _jxl_stub(path: Path, payload: bytes = b"\x00" * 32):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_JXL_SIG + payload)


class _FakeLogger:
    """Collects (level, message) instead of writing files."""

    def __init__(self):
        self.lines = []

    def _n(self, lvl, msg, *a, **k):
        self.lines.append((lvl, msg if not a else msg % a))

    def debug(self, msg, *a, **k):
        self._n("debug", msg, *a, **k)

    def info(self, msg, *a, **k):
        self._n("info", msg, *a, **k)

    def warning(self, msg, *a, **k):
        self._n("warning", msg, *a, **k)

    def error(self, msg, *a, **k):
        self._n("error", msg, *a, **k)


def _ENTRY(src, page=0):
    return (src, page, False, False, 0, False, None)


def _marker_reader(marks):
    """Batched marker read from a fixed map: enough to drive the skip
    classification (_decode_output_is_ours), the gate's match check and the
    dry-run preview from ONE stub."""
    def reader(paths):
        return {str(p): dict(marks.get(str(p)) or {"src": None, "srcsum": None})
                for p in paths}
    return reader


def _reset_run_state(monkeypatch):
    monkeypatch.setattr(dec, "_incomplete_groups", {})
    monkeypatch.setattr(dec, "_mpg_marker_failures", set())
    monkeypatch.setattr(dec, "_group_conflicts", [])
    prev = getattr(dec, "_prev_run_globals", None)
    if prev:
        prev.clear()
    for k in dec._delete_stats:
        dec._delete_stats[k] = 0
    # The stub TIFFs here are not real files: the round-50 pixel check (D5,
    # an edited decode never certifies a deletion) would read them, fail and
    # — failing closed — keep every source. Its own real-codec tests live in
    # test_audit_261008_leftovers.py; these pin the marker gate alone.
    monkeypatch.setattr(dec, "_decoded_tiff_edited", lambda p: None)


def _main_tool_mocks(monkeypatch, fake_logger, tmp_path, captured):
    """The stubs main() needs to run in-process without external tools."""
    monkeypatch.setattr(dec, "setup_logger", lambda: tmp_path / "fake.log")
    monkeypatch.setattr(dec, "logger", fake_logger)
    monkeypatch.setattr(dec, "emit_summary_json",
                        lambda *a, **k: captured.update(k))
    monkeypatch.setattr(dec, "_check_external_tools", lambda *a, **k: None)
    monkeypatch.setattr(dec, "_warn_if_libjxl_too_old", lambda *a, **k: None)


# ===========================================================================
# D-2 — the --delete-skipped gate requires a marker MATCH, fail closed
# ===========================================================================

def _d2_setup(monkeypatch, tmp_path):
    """Common pinning for the process_group delete-gate tests, plus ONE mode-1
    group whose conversion is reported skipped over a PRE-EXISTING TIFF."""
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_SKIPPED", True)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "USE_MATRIX_MODE", False)
    monkeypatch.setattr(dec, "PROVENANCE_CHECK", "path")
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "setup_logger", lambda: tmp_path / "fake.log")
    fake_logger = _FakeLogger()
    monkeypatch.setattr(dec, "logger", fake_logger)

    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "converted_tiff" / "a.tif"
    # Created after the JXL → smart sync sees the existing TIFF as up to date.
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(_JXL_SIG + b"\x00" * 16)
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda *a, **k: (str(src), "skipped", str(final)))
    task = {"type": "multi", "main_jxl": src,
            "entries": [_ENTRY(src)], "ignored_thumbs": [],
            "final_tiff": final}
    return src, final, task


def test_delete_skipped_keeps_source_when_existing_output_marker_mismatches(
        monkeypatch, tmp_path):
    """An existing TIFF carrying a marker for a DIFFERENT source must not
    certify the deletion of these sources (--delete-source --delete-skipped)."""
    _reset_run_state(monkeypatch)
    src, final, task = _d2_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(dec, "_read_source_markers_batch",
                        _marker_reader({str(final): {"src": "unrelated_id",
                                                     "srcsum": None}}))

    results = dec.process_group([task], 1)

    assert results[0][1] == "skipped", results
    assert src.exists(), "an unrelated marker certified the deletion"
    assert final.exists()
    assert dec._delete_stats["kept"] == 1, f"delete stats: {dec._delete_stats}"
    assert dec._delete_stats["deleted_archived"] == 0


def test_delete_skipped_deletes_source_when_existing_output_marker_matches(
        monkeypatch, tmp_path):
    """The matching marker is what works: markers name THESE sources → the
    already-archived source is deleted as before."""
    _reset_run_state(monkeypatch)
    src, final, task = _d2_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(dec, "_read_source_markers_batch",
                        _marker_reader({str(final): {"src": dec._source_path_id(src),
                                                     "srcsum": None}}))

    results = dec.process_group([task], 1)

    assert results[0][1] == "skipped", results
    assert not src.exists(), "a matching marker must allow the finished archive"
    assert final.exists()
    assert dec._delete_stats["deleted_archived"] == 1


def test_dry_run_preview_agrees_with_the_marker_gate(tmp_path, monkeypatch):
    """The --delete-skipped preview must use the SAME match predicate as the
    gate: it may never preview a deletion the gate would refuse (mismatch →
    would-KEEP) and must still preview the matching deletion."""
    _reset_run_state(monkeypatch)
    outcomes = {}
    for name in ("mismatch", "match"):
        d = tmp_path / name
        src = d / "a.jxl"
        _jxl_stub(src)
        # Mode 8: the existing TIFF sits beside the JXL (recursive discovery)
        final = d / "a.tif"
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(_JXL_SIG + b"\x00" * 16)
        marker_src = "unrelated_id" if name == "mismatch" else dec._source_path_id(src)
        outcomes[name] = (src, final,
                          {str(final): {"src": marker_src, "srcsum": None}})
    match_src, _match_final, _ = outcomes["match"]
    mismatch_src, _mismatch_final, _ = outcomes["mismatch"]

    fake_logger = _FakeLogger()
    captured = {}
    _main_tool_mocks(monkeypatch, fake_logger, tmp_path, captured)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: True)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "PROVENANCE_CHECK", "path")

    all_marks = {}
    for _s, _f, m in outcomes.values():
        all_marks.update(m)
    monkeypatch.setattr(dec, "_read_source_markers_batch",
                        _marker_reader(all_marks))
    monkeypatch.setattr(dec, "collect_multipage_groups",
                        lambda jxls: {src: [_ENTRY(src)]
                                      for (src, _, _) in outcomes.values()})

    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(tmp_path), "--mode", "8",
                         "--delete-source", "--delete-skipped", "--dry-run",
                         "--summary-json"])
    dec.main()

    would_delete = [m for _lvl, m in fake_logger.lines if "would DELETE" in m]
    # Mismatch: the existing TIFF is someone else's decode → previewed KEEP.
    assert any("carries no" in m or "would KEEP" in m
               for _lvl, m in fake_logger.lines), \
        f"the mismatch was not previewed as a keep: {fake_logger.lines}"
    assert not any(str(mismatch_src) in m for m in would_delete), \
        f"the preview promised the refused deletion: {fake_logger.lines}"
    # Match: the same predicate still previews the finished-archive delete.
    assert any(str(match_src) in m
               for m in would_delete), \
        f"the preview stopped previewing the matching delete: {fake_logger.lines}"


# ===========================================================================
# D-1 — run-scoped globals restore between in-process main() runs
# ===========================================================================

def _run_dec_main(monkeypatch, tmp_path, src_dir, extra_args):
    fake_logger = _FakeLogger()
    captured = {}
    _main_tool_mocks(monkeypatch, fake_logger, tmp_path, captured)
    monkeypatch.setattr(dec, "collect_multipage_groups",
                        lambda jxls: {src_dir / "a.jxl": [_ENTRY(src_dir / "a.jxl")]})
    monkeypatch.setattr(dec, "_read_source_markers_batch", _marker_reader({}))
    monkeypatch.setattr(dec, "process_group",
                        lambda tasks, workers, *a, **k:
                        [(str(t["main_jxl"]), "ok", str(t["final_tiff"]))
                         for t in tasks])
    _jxl_stub(src_dir / "a.jxl")
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(src_dir), "--mode", "0"]
                        + list(extra_args))
    try:
        dec.main()
    except SystemExit:
        pass
    return fake_logger, captured


def test_main_globals_reset_between_runs(tmp_path, monkeypatch):
    """First run with --delete-source (dry run), second plain: the second
    run's run-scoped globals must be the defaults."""
    _reset_run_state(monkeypatch)
    _run_dec_main(monkeypatch, tmp_path, tmp_path / "run1",
                  ["--delete-source", "--delete-skipped", "--delete-confirm-off",
                   "--no-preview", "--matrix", "--dry-run", "--summary-json"])
    assert dec.DELETE_SOURCE is True, "fixture broken: run 1 did not arm delete"
    assert dec.DELETE_CONFIRM is False

    _run_dec_main(monkeypatch, tmp_path, tmp_path / "run2", [])
    assert dec.DELETE_SOURCE == _SCRIPT_SETTINGS["DELETE_SOURCE"], \
        "DELETE_SOURCE leaked into run 2"
    assert dec.DELETE_SKIPPED == _SCRIPT_SETTINGS["DELETE_SKIPPED"], \
        "DELETE_SKIPPED leaked into run 2"
    assert dec.DELETE_CONFIRM == _SCRIPT_SETTINGS["DELETE_CONFIRM"], \
        "DELETE_CONFIRM leaked into run 2"
    assert dec.ADD_JPEG_PREVIEW == _SCRIPT_SETTINGS["ADD_JPEG_PREVIEW"], \
        "ADD_JPEG_PREVIEW leaked into run 2"
    assert dec.USE_MATRIX_MODE == _SCRIPT_SETTINGS["USE_MATRIX_MODE"], \
        "USE_MATRIX_MODE leaked into run 2"


def test_empty_export_marker_is_honored(tmp_path, monkeypatch):
    """(#433's decoder twin) --export-marker "" means "export NOTHING": an empty
    marker matches nothing. `if args.export_marker:` silently kept the default,
    so a mode-6/7 run anchored on _EXPORT anyway. The next run without the
    flag gets the script's own setting back."""
    _reset_run_state(monkeypatch)
    default = dec.EXPORT_MARKER
    (tmp_path / "tree" / "_EXPORT").mkdir(parents=True)
    _jxl_stub(tmp_path / "tree" / "_EXPORT" / "b.jxl")
    _run_dec_main(monkeypatch, tmp_path, tmp_path / "run1",
                  ["--mode", "6", "--export-marker", "", "--dry-run"])
    assert dec.EXPORT_MARKER == "", "an explicit empty marker was ignored"
    assert dec.find_jxls_mode6(tmp_path / "tree") == []

    _run_dec_main(monkeypatch, tmp_path, tmp_path / "run2", ["--dry-run"])
    assert dec.EXPORT_MARKER == default, "the empty marker leaked into run 2"


def test_round37_dry_run_then_plain_run_leaves_no_leak(tmp_path, monkeypatch):
    """The round37 leak: a dry-run main() call with --delete-source used to
    leave dec.DELETE_SOURCE armed for every later in-process run."""
    _reset_run_state(monkeypatch)
    # Equivalent of test_audit_round37's decoder repro: dry run with
    # --delete-source; the markers cannot prove anything → would-refuse.
    _run_dec_main(monkeypatch, tmp_path, tmp_path / "repro",
                  ["--delete-source", "--dry-run", "--summary-json"])
    assert dec.DELETE_SOURCE is True, "fixture broken"

    # A later plain run must start from the defaults.
    _run_dec_main(monkeypatch, tmp_path, tmp_path / "plain", [])
    assert dec.DELETE_SOURCE is False, "DELETE_SOURCE leaked across runs"


# ===========================================================================
# D-3 — #432: the HHMM confirmation is charged only when the plan deletes
# ===========================================================================

def test_confirm_not_charged_on_all_skip_plan(tmp_path, monkeypatch):
    """A no-TTY re-run of an already-archived folder (--delete-source, no
    --delete-skipped) must not ask for the token it cannot answer."""
    _reset_run_state(monkeypatch)
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_CONFIRM", True)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "USE_MATRIX_MODE", False)
    monkeypatch.setattr(dec, "PROVENANCE_CHECK", "path")

    fake_logger = _FakeLogger()
    captured = {}
    _main_tool_mocks(monkeypatch, fake_logger, tmp_path, captured)

    src_dir = tmp_path / "in"
    src = src_dir / "a.jxl"
    _jxl_stub(src)
    # The existing TIFF is newer and IS this decoder's output → pure SKIP plan.
    final = src_dir / "a.tif"
    final.write_bytes(_JXL_SIG + b"\x00" * 16)
    monkeypatch.setattr(dec, "collect_multipage_groups",
                        lambda jxls: {src: [_ENTRY(src)]})
    monkeypatch.setattr(dec, "_read_source_markers_batch",
                        _marker_reader({str(final): {"src": dec._source_path_id(src),
                                                     "srcsum": None}}))
    calls = []
    monkeypatch.setattr(dec, "confirm_deletion_jxl", lambda: calls.append(1) or True)
    monkeypatch.setattr(dec, "process_group", lambda tasks, workers, *a, **k: [])

    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(src_dir), "--mode", "0",
                         "--delete-source"])
    try:
        dec.main()
    except SystemExit:
        pass
    assert calls == [], \
        f"the HHMM token was charged on an all-skip plan: {fake_logger.lines}"


def test_confirm_charged_when_plan_deletes(tmp_path, monkeypatch):
    """With a plan that would WRITE (this run's conversion, whose sources the
    gate deletes once ok), the confirmation is still charged."""
    _reset_run_state(monkeypatch)
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_CONFIRM", True)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "USE_MATRIX_MODE", False)
    monkeypatch.setattr(dec, "PROVENANCE_CHECK", "path")

    fake_logger = _FakeLogger()
    captured = {}
    _main_tool_mocks(monkeypatch, fake_logger, tmp_path, captured)

    src_dir = tmp_path / "in"
    src = src_dir / "a.jxl"
    _jxl_stub(src)                    # no existing TIFF → a real conversion
    monkeypatch.setattr(dec, "collect_multipage_groups",
                        lambda jxls: {src: [_ENTRY(src)]})
    monkeypatch.setattr(dec, "_read_source_markers_batch", _marker_reader({}))
    calls = []
    monkeypatch.setattr(dec, "confirm_deletion_jxl", lambda: calls.append(1) or True)
    monkeypatch.setattr(dec, "process_group", lambda tasks, workers, *a, **k: [])

    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_decoder.py", str(src_dir), "--mode", "0",
                         "--delete-source"])
    try:
        dec.main()
    except SystemExit:
        pass
    assert calls == [1], f"the planned deletion was never confirmed: {fake_logger.lines}"


# ===========================================================================
# D-5 — no probe files + roundtrip → refuse the paste, fail closed
# ===========================================================================

def test_roundtrip_refuses_when_djxl_wrote_no_probes(tmp_path, monkeypatch):
    """djxl older/odd enough to write neither --icc_out nor --orig_icc_out
    cannot prove the pixels kept the file's own colour space: pasting the
    original ICC on the result (the linear-sRGB trap on a LOSSY ICC-blob
    file) must refuse with a clear error, not silently paste."""
    monkeypatch.setattr(dec, "LOG_DIR", tmp_path)
    dec.setup_logger()
    src = tmp_path / "p.jxl"
    src.write_bytes(_JXL_SIG + b"\x00" * 32)
    monkeypatch.setattr(dec, "FORCE_NONE_MODE", False)
    monkeypatch.setattr(dec, "USE_MATRIX_MODE", False)
    monkeypatch.setattr(dec, "FORCE_BASIC_MODE", False)
    monkeypatch.setattr(dec, "get_source_icc", lambda *a, **k: (b"A" * 300, "xmp"))
    monkeypatch.setattr(dec, "decode_auto_png", lambda *a, **k: None)
    monkeypatch.setattr(dec, "_decoded_in_original_space", lambda a, b: None)

    def _no_paste(*a, **k):
        raise AssertionError("the original ICC must not be pasted when the "
                             "probes are missing")
    monkeypatch.setattr(dec, "read_png_to_numpy", _no_paste)

    with pytest.raises(RuntimeError) as exc:
        dec.decode_jxl_to_numpy(src, tmp_path)
    msg = str(exc.value)
    assert "0.11.2" in msg and "icc_out" in msg, msg


# ===========================================================================
# D-6 — kept is counted per SOURCE in every branch
# ===========================================================================

def test_kept_stats_count_sources_for_missing_final(monkeypatch, tmp_path):
    """A TWO-source group whose final output is missing must report
    kept == 2, not 1 (the gate keeps the whole group; the summary says files)."""
    _reset_run_state(monkeypatch)
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_CONFIRM", False)
    monkeypatch.setattr(dec, "TEMP2_DIR", None)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "setup_logger", lambda: tmp_path / "fake.log")

    s1 = tmp_path / "scan.jxl"
    s2 = tmp_path / "scan_page1.jxl"
    _jxl_stub(s1)
    _jxl_stub(s2)
    final = tmp_path / "out" / "scan.tif"           # never written
    task = {"type": "multi", "main_jxl": s1,
            "entries": [_ENTRY(s1, 0), _ENTRY(s2, 1)],
            "ignored_thumbs": [], "final_tiff": final}
    monkeypatch.setattr(dec, "convert_multipage_jxl_group",
                        lambda *a, **k: (str(s1), "ok", str(final)))

    results = dec.process_group([task], 1)

    assert results[0][1] == "ok", results
    assert dec._delete_stats["kept"] == 2, \
        f"kept mixed groups and sources: {dec._delete_stats}"
