#!/usr/bin/env python3
"""Round 43 — encoder fixes from the 261001 audit (sections 0/1/2 + Errata).

  E-1  --delete-skipped must certify a skipped source by the existing output's
        provenance markers MATCHING the source (jxlphoto-src/srcsum), not just
        by exists()+integrity; the dry-run preview runs the SAME predicate.
        Markerless legacy archives are KEPT fail-closed, with a healing hint.
  E-2  --export-marker "" is honored (empty marker -> matches nothing), a port
        of #433. --export-subfolder already used `is not None` and needed no
        change.
  D-3  #432's encoder twin: the delete confirmation (HHMM/yes) is charged only
        when the plan can actually delete something; a no-TTY re-run of an
        already-archived folder must not exit 3 forever.
  E-3  legacy group-id adoption compares sibling names case-insensitively
        (normcase, like the stale-split check).
  E-4  a user caption starting with "[minor]"/"[major]/Warning:" survives into
        the re-seeded description: stdout is no longer filtered by prefix,
        exiftool's warnings are on stderr.
  Class run-scoped globals reset unconditionally at main() entry (the
        recompressor's _RUN_DEFAULTS pattern; the encoder's flags were
        assigned one-way).

Codecs/exiftool are stubbed; the decoder/recompressor round-43 suites pin the
same predicate classes on their scripts. Pre-fix proof: extract the HEAD copy
of the encoder next to this file's parent and point JXL_ENCODER_UNDER_TEST at
it:

    git show HEAD:jxl_tiff_encoder.py > <tmp>/jxl_tiff_encoder.py
    $env:JXL_ENCODER_UNDER_TEST = "<tmp>/jxl_tiff_encoder.py"
    python -m pytest tests/test_round43_encoder_gate.py

Every E-1/E-2/D-3 (armed/unarmed)/E-3/E-4/class-reset test fails there and
passes on the fixed script.
"""

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_ENCODER_UNDER_TEST")
sys.path.insert(0, str(Path(_OVERRIDE).resolve().parent) if _OVERRIDE else str(_REPO))
import jxl_tiff_encoder as enc

_JXL_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n"

# Run-scoped globals this file can arm through in-process main() calls.
# Restored before AND after each test (restoring with a dict the fix adds
# would make the fixture itself depend on the fix - they are literal here).
_RUN_FLAGS = ("OVERWRITE", "DELETE_SOURCE", "DELETE_CONFIRM", "DELETE_SKIPPED",
              "VERIFY_ROUNDTRIP", "PROVENANCE_CHECK", "ADOPT_SCAN",
              "STRIP_METADATA", "TEMP2_DIR", "EXPORT_MARKER", "EXPORT_JXL_FOLDER",
              "EXPORT_TIFF_SUBFOLDER", "EXCLUDE_FOLDERS", "EMBED_JPEG_THUMBNAIL",
              "MULTIPAGE_TIFF_MODE", "CJXL_DISTANCE")
_DEFAULTS = {
    "OVERWRITE": "smart", "DELETE_SOURCE": False, "DELETE_CONFIRM": True,
    "DELETE_SKIPPED": False, "VERIFY_ROUNDTRIP": False,
    "PROVENANCE_CHECK": "path", "ADOPT_SCAN": True, "STRIP_METADATA": False,
    "TEMP2_DIR": None, "EXPORT_MARKER": "_EXPORT", "EXPORT_JXL_FOLDER": "16B_JXL",
    "EXPORT_TIFF_SUBFOLDER": "", "EXCLUDE_FOLDERS": (),
    "EMBED_JPEG_THUMBNAIL": False, "MULTIPAGE_TIFF_MODE": "split",
    "CJXL_DISTANCE": 0.1,
}


class _FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


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
def _clean_encoder_globals():
    def _apply():
        for name, value in _DEFAULTS.items():
            setattr(enc, name, value)
        for k in enc._delete_stats:
            enc._delete_stats[k] = 0
        enc._counter["done"] = 0
        enc._counter["total"] = 0
        enc._reset_abort()
        with enc._multipage_ignored_lock:
            enc._discarded_real_page_sources.clear()
            enc._discarded_thumb_sources.clear()
            enc._multipage_ignored.update(files=0, pages=0)
            enc._thumbnails_dropped.update(files=0, pages=0)
    _apply()
    yield
    _apply()


def _tiff(path: Path, value=1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((16, 16, 3), value, np.uint16),
                     photometric="rgb")


def _jxl_stub(path: Path, payload=b"\x00" * 32):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_JXL_SIG + payload)


def _make_newer(target: Path, than: Path):
    stamp = than.stat().st_mtime + 100
    os.utime(target, (stamp, stamp))


def _marker_reader(marks):
    def reader(paths):
        return {str(p): dict(marks.get(str(p)) or {"src": None, "srcsum": None})
                for p in paths}
    return reader


# ===========================================================================
# E-1 — the skip path of the delete gate needs a MATCHING provenance marker
# ===========================================================================

def _e1_run(tmp_path, monkeypatch, *, out_marks, overwrite=None):
    """One TIFF whose page-0 conversion is reported as SKIP over a
    PRE-EXISTING JXL at the final path; real convert_one is stubbed (the
    conversion layer is not under test here)."""
    tiff = tmp_path / "photo.tif"
    _tiff(tiff)
    final = tmp_path / "photo.jxl"
    _jxl_stub(final)
    _make_newer(final, tiff)          # newer: a smart-sync SKIP, not a reconvert
    fake_logger = _FakeLogger()
    monkeypatch.setattr(enc, "logger", fake_logger)
    if overwrite is not None:
        monkeypatch.setattr(enc, "OVERWRITE", overwrite)
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "DELETE_SKIPPED", True)
    monkeypatch.setattr(enc, "PROVENANCE_CHECK", "path")
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    monkeypatch.setattr(enc, "_read_source_markers_batch", _marker_reader(out_marks))

    def _skipped(*a, **k):
        return ((str(a[0]), a[3]), "skipped", str(a[2]), None)
    monkeypatch.setattr(enc, "convert_one", _skipped)

    results = enc.process_group([(tiff, final, 0, False, 0, 3)], 1, mode=8)
    assert results[0][1] == "skipped", results
    return tiff, final


def test_delete_skipped_keeps_master_when_existing_output_is_a_foreign_photo(
        tmp_path, monkeypatch):
    """The E-1 repro, corrected direction (AI2): a valid, NEWER same-named JXL
    of a DIFFERENT photo must not certify deleting the master TIFF."""
    tiff, _final = _e1_run(tmp_path, monkeypatch,
                           out_marks={"other": {"src": "other-photo",
                                                "srcsum": "other-sum"}})
    assert tiff.exists(), "a foreign archive certified the deletion of the master"
    assert enc._delete_stats["kept"] == 1, enc._delete_stats
    assert enc._delete_stats["deleted_archived"] == 0


def test_delete_skipped_deletes_master_when_marker_matches(tmp_path, monkeypatch):
    """A matching marker (the one the encoder itself writes) still deletes the
    already-archived source — the normal --delete-skipped workflow."""
    tiff = tmp_path / "photo.tif"
    final = tmp_path / "photo.jxl"
    _tiff(tiff)
    _jxl_stub(final)
    monkeypatch.setattr(enc, "logger", _FakeLogger())
    monkeypatch.setattr(enc, "DELETE_SOURCE", True)
    monkeypatch.setattr(enc, "DELETE_SKIPPED", True)
    monkeypatch.setattr(enc, "PROVENANCE_CHECK", "path")
    monkeypatch.setattr(enc, "TEMP2_DIR", None)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    out_mark = {"src": enc._source_path_id(tiff), "srcsum": "irrelevant"}
    monkeypatch.setattr(enc, "_read_source_markers_batch",
                        _marker_reader({str(final): out_mark}))

    def _skipped(*a, **k):
        return ((str(a[0]), a[3]), "skipped", str(a[2]), None)
    monkeypatch.setattr(enc, "convert_one", _skipped)

    enc.process_group([(tiff, final, 0, False, 0, 3)], 1, mode=8)
    assert not tiff.exists(), "a matching marker must not block the finished archive"
    assert enc._delete_stats["deleted_archived"] == 1, enc._delete_stats


def test_delete_skipped_keeps_markerless_legacy_archive_with_healing_hint(
        tmp_path, monkeypatch):
    """A legacy archive predating the markers cannot prove anything → KEPT,
    and the log tells the user how to heal it."""
    tiff, final = _e1_run(tmp_path, monkeypatch, out_marks={})
    assert tiff.exists(), "a markerless archive was waved through"
    assert enc._delete_stats["kept"] == 1
    assert any("provenance" in w and "re-encode" in w for w in enc.logger.warnings), \
        enc.logger.warnings


def test_delete_skipped_overwrite_false_path_needs_the_marker_too(
        tmp_path, monkeypatch):
    """OVERWRITE=False's "SKIP (exists)" admits a skip with NO age check at
    all — the other half of the E-1 hole; its deletion needs the same proof."""
    tiff, final = _e1_run(tmp_path, monkeypatch,
                          out_marks={str(tmp_path / "photo.jxl"):
                                     {"src": "other-photo", "srcsum": None}},
                          overwrite=False)
    assert tiff.exists()


def test_delete_gate_marker_failure_is_loud_not_silent(tmp_path, monkeypatch):
    """The unproved KEEP names the cause and how to heal, instead of blending
    into the generic integrity-failure line."""
    tiff, _final = _e1_run(tmp_path, monkeypatch, out_marks={})  # unreadable == no marker
    fake = enc.logger  # installed by _e1_run
    assert any("provenance" in w for w in fake.warnings), fake.warnings
    assert any("adopt" in w or "re-encode" in w for w in fake.warnings)


# ===========================================================================
# E-1 mirror — the dry-run preview keeps the same predicate (no drift)
# ===========================================================================

def test_dry_run_preview_matches_the_marker_gate(tmp_path, monkeypatch):
    """The preview must not promise the deletion of a master whose same-named
    archive carries a foreign marker; and must still promise the delete that
    the matching marker allows."""
    tree = tmp_path / "tree"
    outcomes = {}
    for name in ("foreign", "match"):
        d = tree / name
        t = d / f"{name}.tif"
        _tiff(t)
        os.utime(t, (1000, 1000))
        j = d / f"{name}.jxl"
        _jxl_stub(j)
        _make_newer(j, t)
        out_mark = ({"src": "unrelated-id", "srcsum": None}
                    if name == "foreign"
                    else {"src": enc._source_path_id(t), "srcsum": None})
        outcomes[name] = (t, j, out_mark)

    fake = _FakeLogger()
    monkeypatch.setattr(enc, "setup_logger", lambda: tree / "fake.log")
    monkeypatch.setattr(enc, "logger", fake)
    monkeypatch.setattr(enc, "emit_summary_json", lambda *a, **k: None)
    monkeypatch.setattr(enc, "_min_effective_distance", lambda exe: 0.05)
    monkeypatch.setattr(enc, "_verify_jxl_integrity", lambda p: True)
    all_marks = {}
    for _t, _j, m in outcomes.values():
        all_marks[str(_j)] = m
    monkeypatch.setattr(enc, "_read_source_markers_batch", _marker_reader(all_marks))

    foreign_src, match_src = outcomes["foreign"][0], outcomes["match"][0]
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(tree), "--mode", "8",
                         "--delete-source", "--delete-skipped", "--dry-run"])
    enc.main()

    would_delete = [m for m in fake.warnings if "would DELETE" in m]
    kept = [m for m in fake.infos if "would KEEP" in m]
    assert not any(str(foreign_src) in m for m in would_delete), \
        f"the preview promised the refused deletion: {fake.warnings}"
    assert any("provenance" in m and str(foreign_src) in m for m in kept), \
        f"the foreign keep lost its reason: {kept}"
    assert any(str(match_src) in m for m in would_delete), \
        f"the preview stopped promising the proved deletion: {fake.warnings}"


# ===========================================================================
# E-2 — --export-marker "" is honored (empty marker matches nothing)
# ===========================================================================

@pytest.mark.parametrize("mode", [6, 7])
def test_empty_export_marker_finds_nothing(tmp_path, monkeypatch, mode):
    tree = tmp_path
    _tiff(tree / "_EXPORT" / "TIFF16" / "a.tif")
    fake = _FakeLogger()
    monkeypatch.setattr(enc, "setup_logger", lambda: tree / "fake.log")
    monkeypatch.setattr(enc, "logger", fake)
    monkeypatch.setattr(enc, "_min_effective_distance", lambda exe: 0.05)
    captured = []
    monkeypatch.setattr(enc, "emit_summary_json",
                        lambda enabled, **kw: captured.append(kw))
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(tree), "--mode", str(mode),
                         "--export-marker", "", "--dry-run", "--summary-json"])
    enc.main()

    assert enc.EXPORT_MARKER == "", "an explicit empty marker was ignored"
    assert captured[-1]["ok"] == 0, captured[-1]
    assert captured[-1]["skipped"] == 0, captured[-1]
    assert enc.find_tiffs_mode6(tree) == [], "an empty marker cannot anchor anything"


def test_default_export_marker_still_finds_the_tree(tmp_path, monkeypatch):
    """Control: without the flag, modes 6 still anchor on _EXPORT."""
    tree = tmp_path
    _tiff(tree / "2024_EXPORT" / "TIFF16" / "a.tif")
    fake = _FakeLogger()
    monkeypatch.setattr(enc, "setup_logger", lambda: tree / "fake.log")
    monkeypatch.setattr(enc, "logger", fake)
    monkeypatch.setattr(enc, "_min_effective_distance", lambda exe: 0.05)
    captured = []
    monkeypatch.setattr(enc, "emit_summary_json",
                        lambda enabled, **kw: captured.append(kw))
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(tree), "--mode", "6",
                         "--dry-run", "--summary-json"])
    enc.main()
    assert captured[-1]["ok"] == 1, captured[-1]


# ===========================================================================
# D-3 — the delete confirmation is charged only when the plan deletes
# ===========================================================================

def _main_plumbing(monkeypatch, tmp_path, fake_logger):
    monkeypatch.setattr(enc, "setup_logger", lambda: tmp_path / "fake.log")
    monkeypatch.setattr(enc, "logger", fake_logger)
    monkeypatch.setattr(enc, "emit_summary_json", lambda *a, **k: None)
    monkeypatch.setattr(enc, "_min_effective_distance", lambda exe: 0.05)
    monkeypatch.setattr(enc, "_get_cjxl_cmd", lambda: "cjxl")
    monkeypatch.setattr(enc, "_get_exiftool_cmd", lambda: "exiftool")
    monkeypatch.setattr(enc.shutil, "which", lambda *a, **k: "C:/tools/x")
    monkeypatch.setattr(enc, "_warn_if_libjxl_too_old", lambda *a, **k: None)
    monkeypatch.setattr(enc, "process_group", lambda items, workers, mode=0: [])


def test_confirm_not_charged_on_an_all_skip_plan(tmp_path, monkeypatch):
    """A no-TTY re-run of an already-archived folder (--delete-source, no
    --delete-skipped): nothing would be deleted, so the token must not fire."""
    src_dir = tmp_path / "in"
    t = src_dir / "a.tif"
    _tiff(t)
    os.utime(t, (1000, 1000))
    j = src_dir / "a.jxl"
    _jxl_stub(j)
    _make_newer(j, t)     # smart sync: pure SKIP

    _main_plumbing(monkeypatch, tmp_path, _FakeLogger())
    calls = []
    monkeypatch.setattr(enc, "confirm_deletion_tiff",
                        lambda lossy: calls.append(1) or True)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(src_dir), "--mode", "8",
                         "--delete-source", "--no-preflight"])
    enc.main()
    assert calls == [], "the HHMM token was charged for a plan with no deletion"


def test_confirm_still_charged_when_a_conversion_will_delete(tmp_path, monkeypatch):
    """With a real conversion planned (final does not exist), the confirmation
    is charged exactly as before."""
    src_dir = tmp_path / "in"
    _tiff(src_dir / "a.tif")          # no existing JXL: a real conversion

    _main_plumbing(monkeypatch, tmp_path, _FakeLogger())
    calls = []
    monkeypatch.setattr(enc, "confirm_deletion_tiff",
                        lambda lossy: calls.append(1) or True)
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(src_dir), "--mode", "8",
                         "--delete-source", "--no-preflight"])
    enc.main()
    assert calls == [1], "a planned deletion ran without"


def test_plan_would_delete_predicate(tmp_path, monkeypatch):
    """The predicate itself: a would-write item charges, a fully-skipped plan
    does not (unless --delete-skipped is armed — fail closed)."""
    src = tmp_path / "a.tif"
    _tiff(src)
    j = tmp_path / "a.jxl"
    _tiff(j)                          # exists, and is NEWER: a would-skip
    item = [(src, j, 0, False, 0, 3)]

    monkeypatch.setattr(enc, "OVERWRITE", "smart")
    monkeypatch.setattr(enc, "DELETE_SKIPPED", False)
    assert enc._plan_would_delete_source(item) is False
    monkeypatch.setattr(enc, "DELETE_SKIPPED", True)
    assert enc._plan_would_delete_source(item) is True
    # An item that would be written charges the prompt in both states.
    naked = [(src, tmp_path / "missing.jxl", 0, False, 0, 3)]
    monkeypatch.setattr(enc, "DELETE_SKIPPED", False)
    assert enc._plan_would_delete_source(naked) is True


# ===========================================================================
# E-3 — legacy group-id adoption matches sibling names case-insensitively
# ===========================================================================

def test_legacy_group_id_adopted_from_case_mismatched_sibling(tmp_path, monkeypatch):
    """`scan_page2.jxl` on disk next to a re-split `Scan.tif` must heal: the
    stale-split check normcases; the adoption check used to be case-sensitive."""
    dest = tmp_path / "out"
    dest.mkdir()
    sibling = dest / "scan_page2.jxl"
    _jxl_stub(sibling)
    tiff = tmp_path / "Scan.tif"
    tiff.write_bytes(b"any")          # only the stem is consulted

    legacy = enc._legacy_group_id(tiff)
    monkeypatch.setattr(enc, "_read_group_markers_batch",
                        lambda paths: {str(p): legacy for p in paths})
    adopted = enc._adopt_legacy_group_ids(
        {"k": tiff}, {"k": dest}, {"k": {(2, False)}})
    assert adopted == {"k": legacy}, \
        "the case-mismatched legacy sibling was not adopted; the archive will split"


def test_a_foreign_sibling_is_still_not_adopted(tmp_path, monkeypatch):
    """Control — unanimity still holds: a sibling carrying another id refuses
    the adoption (in both the pre- and post-fix code)."""
    dest = tmp_path / "out"
    dest.mkdir()
    sibling = dest / "scan_page2.jxl"
    _jxl_stub(sibling)
    tiff = tmp_path / "Scan.tif"
    tiff.write_bytes(b"any")
    monkeypatch.setattr(enc, "_read_group_markers_batch",
                        lambda paths: {str(p): "someone-elses-id" for p in paths})
    adopted = enc._adopt_legacy_group_ids(
        {"k": tiff}, {"k": dest}, {"k": {(2, False)}})
    assert adopted == {}
    # A same-case sibling still adopts (guard against a regression in the fix).
    same = tmp_path / "dest2"
    same.mkdir()
    _jxl_stub(same / "Scan_page2.jxl")
    monkeypatch.setattr(enc, "_read_group_markers_batch",
                        lambda paths: {str(p): enc._legacy_group_id(tiff)
                                       for p in paths})
    adopted2 = enc._adopt_legacy_group_ids(
        {"k": tiff}, {"k": same}, {"k": {(2, False)}})
    assert adopted2 == {"k": enc._legacy_group_id(tiff)}


# ===========================================================================
# E-4 — a caption beginning with a warning word survives the lineage re-seed
# ===========================================================================

def test_caption_starting_with_minor_survives(tmp_path, monkeypatch):
    """exiftool -s3 prints the value only; its warnings go to stderr. A
    caption "[minor] dust on scan" must not be dropped as if it were a
    warning — with --delete-source it existed only on the deleted TIFF."""
    xmp = tmp_path / "a_original.xmp"
    xmp.write_bytes(b"<x/>")          # must exist; exiftool is stubbed away
    captured = {}

    def fake_run(lines, timeout=60):
        captured["lines"] = list(lines)
        return _FakeRun(stdout="[minor] dust on scan\n", stderr="", returncode=0)
    monkeypatch.setattr(enc, "_run_exiftool_argfile", fake_run)
    assert enc.read_existing_description(xmp) == "[minor] dust on scan"
    # The stub only models the bare value because -s3 is what prints one;
    # plain -s prints "Description : value" (see the real-exiftool test).
    assert "-s3" in captured["lines"] and "-s" not in captured["lines"]


_XMP_WITH_CAPTION = """<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF
 xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
<rdf:Description rdf:about='' xmlns:dc='http://purl.org/dc/elements/1.1/'>
<dc:description><rdf:Alt><rdf:li xml:lang='x-default'>{caption}</rdf:li></rdf:Alt>
</dc:description></rdf:Description></rdf:RDF></x:xmpmeta>
<?xpacket end='w'?>
"""


@pytest.mark.skipif(enc._get_exiftool_cmd() is None, reason="exiftool not installed")
@pytest.mark.parametrize("caption", ["[minor] dust on scan",
                                     "Captured: autumn 1958",
                                     "plain caption"])
def test_caption_read_back_verbatim_with_real_exiftool(tmp_path, caption):
    """REAL exiftool (the stubs above cannot catch this): the round-43 fix
    first passed `-s`, which prints "Description                     : value",
    and returned that line verbatim — the TAG NAME was seeded into every new
    dc:Description. The value must come back exactly as written."""
    xmp = tmp_path / "a_original.xmp"
    xmp.write_text(_XMP_WITH_CAPTION.format(caption=caption), encoding="utf-8")
    assert enc.read_existing_description(xmp) == caption


def test_caption_starting_with_major_survives(tmp_path, monkeypatch):
    xmp = tmp_path / "a_original.xmp"
    xmp.write_bytes(b"<x/>")
    monkeypatch.setattr(enc, "_run_exiftool_argfile",
                        lambda lines, timeout=60:
                        _FakeRun(stdout="[major] retouched 2024\n",
                                 stderr="", returncode=0))
    assert enc.read_existing_description(xmp) == "[major] retouched 2024"


def test_caption_starting_with_warning_survives(tmp_path, monkeypatch):
    """Words that exiftool prefixes ITS messages with are captions too; the
    tool's own warnings live on stderr, which no longer gate the value."""
    xmp = tmp_path / "a_original.xmp"
    xmp.write_bytes(b"<x/>")
    monkeypatch.setattr(enc, "_run_exiftool_argfile",
                        lambda lines, timeout=60:
                        _FakeRun(stdout="Warning: this scan still needs dust "
                                       "cloning\n", stderr="", returncode=0))
    assert enc.read_existing_description(xmp) == \
        "Warning: this scan still needs dust cloning"


def test_stderr_warnings_no_longer_pollute_the_value(tmp_path, monkeypatch):
    """A real tool warning on stderr does not reach the parsed stdout."""
    xmp = tmp_path / "a_original.xmp"
    xmp.write_bytes(b"<x/>")
    monkeypatch.setattr(enc, "_run_exiftool_argfile",
                        lambda lines, timeout=60:
                        _FakeRun(stdout="caption text\n",
                                 stderr="[minor] format warning\n",
                                 returncode=0))
    assert enc.read_existing_description(xmp) == "caption text"


# ===========================================================================
# Class fix — run-scoped globals reset at main() entry
# ===========================================================================

def test_second_main_run_falls_back_to_defaults(tmp_path, monkeypatch):
    src_dir = tmp_path / "in"
    src_dir.mkdir()

    _main_plumbing(monkeypatch, tmp_path, _FakeLogger())
    stg = tmp_path / "staging"

    # Run 1 — arm deletion (no confirmation), verify-roundtrip, strip, staging,
    # marker and provenance overrides.
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(src_dir), "--mode", "8",
                         "--delete-source", "--delete-confirm-off",
                         "--delete-skipped", "--verify-roundtrip", "--strip",
                         "--provenance", "content", "--no-adopt-scan",
                         "--embed-thumbnail", "--export-marker", "_X",
                         "--staging", str(stg), "--dry-run", "--summary-json"])
    enc.main()
    assert enc.DELETE_SOURCE is True, "fixture broken: run 1 did not arm delete"
    assert enc.DELETE_CONFIRM is False
    assert enc.DELETE_SKIPPED is True
    assert enc.VERIFY_ROUNDTRIP is True
    assert enc.STRIP_METADATA is True
    assert enc.PROVENANCE_CHECK == "content"
    assert enc.ADOPT_SCAN is False
    assert enc.EMBED_JPEG_THUMBNAIL is True
    assert enc.EXPORT_MARKER == "_X"
    assert enc.TEMP2_DIR == str(stg)

    # Run 2 — plain. Every flag above must fall back to its script setting.
    monkeypatch.setattr(sys, "argv",
                        ["jxl_tiff_encoder.py", str(src_dir), "--mode", "8",
                         "--no-preflight"])
    enc.main()
    assert enc.DELETE_SOURCE is False, "--delete-source leaked into run 2"
    assert enc.DELETE_CONFIRM is True, "--delete-confirm-off leaked into run 2"
    assert enc.DELETE_SKIPPED is False
    assert enc.VERIFY_ROUNDTRIP is False
    assert enc.STRIP_METADATA is False
    assert enc.PROVENANCE_CHECK == "path"
    assert enc.ADOPT_SCAN is True
    assert enc.EMBED_JPEG_THUMBNAIL is False
    assert enc.EXPORT_MARKER == "_EXPORT"
    assert enc.TEMP2_DIR is None, "--staging leaked into run 2"
    assert enc.OVERWRITE == "smart"
