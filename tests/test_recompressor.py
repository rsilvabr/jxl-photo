#!/usr/bin/env python3
"""Tests for jxl_recompressor.py — the JXL -> JXL recompression path.

Unit-level, with cjxl/exiftool mocked: the matrix that compares the recorded
cjxl d=/e= against the request, the downgrade/unknown/jbrd policies, the
keep-smaller fallback, the metadata restamp, and the delete gates.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_recompressor as rec


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    """Every test starts from the documented defaults and a cleared abort latch."""
    monkeypatch.setattr(rec, "OVERWRITE", "smart")
    monkeypatch.setattr(rec, "CJXL_DISTANCE", 1.0)
    monkeypatch.setattr(rec, "CJXL_EFFORT", 7)
    monkeypatch.setattr(rec, "ON_DOWNGRADE", "ask")
    monkeypatch.setattr(rec, "ON_UNKNOWN", "convert")
    monkeypatch.setattr(rec, "JBRD_POLICY", "copy")
    monkeypatch.setattr(rec, "KEEP_SMALLER", True)
    monkeypatch.setattr(rec, "ENCODE_TAG_MODE", "xmp")
    monkeypatch.setattr(rec, "DELETE_SOURCE", False)
    monkeypatch.setattr(rec, "DELETE_SKIPPED", False)
    monkeypatch.setattr(rec, "VERIFY_ROUNDTRIP", False)
    monkeypatch.setattr(rec, "TEMP2_DIR", None)
    monkeypatch.setattr(rec, "TEMP_DIR", None)
    rec._reset_abort()
    yield


# ---------------------------------------------------------------------------
# Synthetic JXL containers (no codec needed: the integrity/box checks only
# parse the ISOBMFF structure)
# ---------------------------------------------------------------------------

def _fake_jxl_bytes(size: int = 1024, jbrd: bool = False) -> bytes:
    """Minimal well-formed container: signature + jxlc box (+ optional jbrd)."""
    sig = b"\x00\x00\x00\x0cJXL \x0d\x0a\x87\x0a"
    boxes = b""
    if jbrd:
        boxes += (16).to_bytes(4, "big") + b"jbrd" + b"\x00" * 8
    remaining = size - len(sig) - len(boxes)
    assert remaining >= 8
    boxes += remaining.to_bytes(4, "big") + b"jxlc" + b"\x00" * (remaining - 8)
    return sig + boxes


def _fake_jxl(path: Path, size: int = 1024, jbrd: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_fake_jxl_bytes(size, jbrd=jbrd))
    return path


# ---------------------------------------------------------------------------
# Encode-parameter record: parse / strip / restamp
# ---------------------------------------------------------------------------

class _FakeExiftoolRun:
    def __init__(self, stdout="", stderr=b"", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestReadEncodeParamsBatch:
    """The batch reader is what replaced the old `_parse_encode_params`
    helper (removed as dead code, #22 round 40): these keep the SAME parse
    rules covered — the last entry of the append-only chain wins, and
    unparseable or absent records stay None — but through the real reader."""

    def _batch(self, monkeypatch, entries):
        run = _FakeExiftoolRun(json.dumps(entries))
        monkeypatch.setattr(rec, "subprocess", type(
            "S", (), {"run": staticmethod(lambda *a, **k: run),
                      "TimeoutExpired": subprocess.TimeoutExpired}))
        monkeypatch.setattr(rec, "_get_exiftool_cmd", lambda: "exiftool")
        return rec._read_encode_params_batch(["NOFILE.jxl"])

    def test_simple_xmp_tag(self, monkeypatch):
        info = self._batch(monkeypatch, [{
            "SourceFile": "NOFILE.jxl",
            "Description": "cjxl d=0.1 e=7",
            "Software": "",
        }])
        assert info["NOFILE.jxl"]["params"] == (0.1, 7)

    def test_software_chain_last_match_wins(self, monkeypatch):
        # Encoder concatenates "old | new"; the LAST tag describes the file.
        info = self._batch(monkeypatch, [{
            "SourceFile": "NOFILE.jxl",
            "Description": "Capture One 23 | cjxl d=0.5 e=7",
            "Software": "",
        }])
        assert info["NOFILE.jxl"]["params"] == (0.5, 7)

    def test_lossless_distance(self, monkeypatch):
        info = self._batch(monkeypatch, [{
            "SourceFile": "NOFILE.jxl",
            "Description": "cjxl d=0 e=9",
            "Software": "",
        }])
        assert info["NOFILE.jxl"]["params"] == (0.0, 9)

    def test_no_tag_is_none(self, monkeypatch):
        for desc in ("My summer vacation", "", None):
            entries = [{"SourceFile": "NOFILE.jxl", "Description": desc, "Software": ""}]
            info = self._batch(monkeypatch, entries)
            assert info["NOFILE.jxl"]["params"] is None

    def test_garbage_is_none(self, monkeypatch):
        info = self._batch(monkeypatch, [{
            "SourceFile": "NOFILE.jxl",
            "Description": "cjxl d=abc e=7",
            "Software": "",
        }])
        assert info["NOFILE.jxl"]["params"] is None


class TestStripEncodeParams:
    def test_strips_tag_keeps_caption(self):
        assert rec._strip_encode_params("My caption | cjxl d=0.1 e=7") == ("My caption", 0)

    def test_strips_bare_tag_to_empty(self):
        assert rec._strip_encode_params("cjxl d=0.1 e=7") == ("", 0)

    def test_no_tag_unchanged(self):
        assert rec._strip_encode_params("CreatorTool 1.0") == ("CreatorTool 1.0", 0)

    def test_dangling_pipes_removed(self):
        assert rec._strip_encode_params("cjxl d=0.1 e=7 | Notes") == ("Notes", 0)


class TestRestampArgs:
    def test_xmp_mode_appends_to_chain_keeps_caption(self):
        lines = rec._restamp_args("My caption | cjxl d=0.1 e=7", "")
        assert "-XMP-dc:Description=My caption | gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7" in lines
        assert not any(l.startswith("-Software=") for l in lines)

    def test_xmp_mode_bare_tag(self):
        lines = rec._restamp_args("cjxl d=0.1 e=7", "")
        assert lines == ["-XMP-dc:Description=gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7"]

    def test_xmp_mode_strips_stale_software_tag(self, monkeypatch):
        lines = rec._restamp_args("", "C1 | cjxl d=0.1 e=7")
        # The record MOVES fields: the Software chain is MIGRATED into the
        # dc:Description chain (union, not dropped — dropping it would
        # undercount the generations), and the user text "C1" stays in
        # Software.
        assert "-XMP-dc:Description=gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7" in lines
        assert "-Software=C1" in lines  # machine block removed from Software

    def test_software_mode(self, monkeypatch):
        monkeypatch.setattr(rec, "ENCODE_TAG_MODE", "software")
        lines = rec._restamp_args("cjxl d=0.1 e=7", "C1 | cjxl d=0.1 e=7")
        assert "-Software=C1 | gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7" in lines
        # and the stale XMP tag is stripped, not left to mislead a later run
        assert "-XMP-dc:Description=" in lines

    def test_off_mode_strips_everywhere(self, monkeypatch):
        monkeypatch.setattr(rec, "ENCODE_TAG_MODE", "off")
        lines = rec._restamp_args("Notes | gen=3 | cjxl d=0.1 e=7", "cjxl d=0.1 e=7")
        assert "-XMP-dc:Description=Notes" in lines
        assert "-Software=" in lines
        assert not any("cjxl d=" in l or "gen=" in l for l in lines)

    def test_off_mode_clean_file_adds_nothing(self, monkeypatch):
        monkeypatch.setattr(rec, "ENCODE_TAG_MODE", "off")
        assert rec._restamp_args("Notes", "C1") == []

    def test_newlines_flattened(self):
        lines = rec._restamp_args("line1\nline2 | cjxl d=0.1 e=7", "")
        assert all("\n" not in l and "\r" not in l for l in lines)


# ---------------------------------------------------------------------------
# The decision matrix
# ---------------------------------------------------------------------------

class TestClassify:
    @pytest.mark.parametrize("src,new_d,new_e,cat", [
        # The intended use cases
        ((0.0, 7), 1.0, 7, "ok"),        # lossless source -> first lossy generation
        ((0.0, 7), 0.05, 7, "ok"),
        ((0.1, 7), 1.0, 7, "ok"),        # smaller target
        ((0.05, 7), 2.0, 9, "ok"),
        ((0.0, 7), 0.0, 9, "ok"),        # lossless -> lossless, higher effort
        # Counterproductive
        ((0.0, 7), 0.0, 7, "downgrade"),  # identical lossless settings
        ((0.0, 9), 0.0, 7, "downgrade"),  # lossless, LOWER effort
        ((0.1, 7), 0.1, 7, "downgrade"),  # same distance, same effort
        ((0.1, 7), 0.1, 5, "downgrade"),  # same distance, lower effort: strictly worse
        ((0.1, 7), 0.1, 9, "downgrade"),  # same distance, higher effort: wasted compute
        ((1.0, 7), 0.1, 7, "downgrade"),  # "quality upgrade" is impossible
        ((1.0, 7), 0.0, 7, "downgrade"),  # lossless re-encode of a lossy file: grows
        # Unknown origin
        (None, 1.0, 7, "unknown"),
    ])
    def test_matrix(self, src, new_d, new_e, cat):
        assert rec._classify(src, new_d, new_e)[0] == cat

    def test_reason_mentions_recorded_values(self):
        cat, reason = rec._classify((1.0, 7), 0.1, 7)
        assert "d=1.0" in reason and cat == "downgrade"


class TestPolicyAction:
    def test_ok_always_converts(self):
        assert rec._policy_action("ok", jbrd=False) == "convert"
        assert rec._policy_action("ok", jbrd=True) == "copy"  # jbrd wins

    def test_downgrade_policy(self, monkeypatch):
        monkeypatch.setattr(rec, "ON_DOWNGRADE", "copy")
        assert rec._policy_action("downgrade", jbrd=False) == "copy"
        monkeypatch.setattr(rec, "ON_DOWNGRADE", "convert")
        assert rec._policy_action("downgrade", jbrd=False) == "convert"

    def test_unknown_policy(self, monkeypatch):
        monkeypatch.setattr(rec, "ON_UNKNOWN", "skip")
        assert rec._policy_action("unknown", jbrd=False) == "skip"

    def test_jbrd_convert_falls_through_to_category(self, monkeypatch):
        monkeypatch.setattr(rec, "JBRD_POLICY", "convert")
        monkeypatch.setattr(rec, "ON_DOWNGRADE", "copy")
        assert rec._policy_action("downgrade", jbrd=True) == "copy"
        assert rec._policy_action("ok", jbrd=True) == "convert"


class TestMarkersMatch:
    def test_srcsum_match_in_content_mode(self):
        a = {"src": None, "srcsum": "abc123"}
        b = {"src": "x", "srcsum": "abc123"}
        assert rec._markers_match(a, b, "content") is True

    def test_srcsum_alone_does_not_satisfy_path_mode(self):
        a = {"src": None, "srcsum": "abc123"}
        b = {"src": "x", "srcsum": "abc123"}
        assert rec._markers_match(a, b, "path") is False
        assert rec._markers_match(a, b) is False          # path is the default

    def test_src_match(self):
        a = {"src": "loc1", "srcsum": None}
        b = {"src": "loc1", "srcsum": "different"}
        assert rec._markers_match(a, b) is True

    def test_content_mode_requires_srcsum(self):
        a = {"src": "loc1", "srcsum": None}
        b = {"src": "loc1", "srcsum": "different"}
        assert rec._markers_match(a, b, "content") is False

    def test_mismatch_fails_closed(self):
        a = {"src": "loc1", "srcsum": "sum1"}
        b = {"src": "loc2", "srcsum": "sum2"}
        assert rec._markers_match(a, b) is False

    def test_missing_markers_fail_closed(self):
        assert rec._markers_match({"src": None, "srcsum": None},
                                  {"src": "loc1", "srcsum": "sum1"}) is False


# ---------------------------------------------------------------------------
# Box parsing and integrity (pure bytes, no codec)
# ---------------------------------------------------------------------------

class TestBoxParsing:
    def test_has_jbrd_box(self, tmp_path):
        assert rec.has_jbrd_box(_fake_jxl(tmp_path / "a.jxl", jbrd=True)) is True
        assert rec.has_jbrd_box(_fake_jxl(tmp_path / "b.jxl", jbrd=False)) is False

    def test_bare_codestream_has_no_jbrd(self, tmp_path):
        p = tmp_path / "bare.jxl"
        p.write_bytes(b"\xff\x0a" + b"\x00" * 100)
        assert rec.has_jbrd_box(p) is False

    def test_integrity_accepts_container(self, tmp_path):
        assert rec._verify_jxl_integrity(_fake_jxl(tmp_path / "ok.jxl")) is True

    def test_integrity_refuses_bare_codestream(self, tmp_path):
        p = tmp_path / "bare.jxl"
        p.write_bytes(b"\xff\x0a" + b"\x00" * 100)
        assert rec._verify_jxl_integrity(p) is False

    def test_integrity_refuses_truncated(self, tmp_path):
        p = tmp_path / "trunc.jxl"
        p.write_bytes(_fake_jxl_bytes(1024)[:500])  # box chain no longer ends at EOF
        assert rec._verify_jxl_integrity(p) is False

    def test_integrity_refuses_metadata_only(self, tmp_path):
        sig = b"\x00\x00\x00\x0cJXL \x0d\x0a\x87\x0a"
        xml_box = (20).to_bytes(4, "big") + b"xml " + b"\x00" * 12
        p = tmp_path / "meta.jxl"
        p.write_bytes(sig + xml_box)  # no jxlc/jxlp codestream box
        assert rec._verify_jxl_integrity(p) is False


# ---------------------------------------------------------------------------
# convert_one with cjxl/exiftool mocked
# ---------------------------------------------------------------------------

def _mock_cjxl_writes(monkeypatch, out_size: int, rc: int = 0):
    """Pretend cjxl ran: write a synthetic container at the output path."""
    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 3 and str(cmd[2]).endswith(".jxl"):
            Path(cmd[2]).write_bytes(_fake_jxl_bytes(out_size))
        return subprocess.CompletedProcess(cmd, rc, stdout=b"", stderr=b"")
    monkeypatch.setattr(rec.subprocess, "run", fake_run)


def _mock_exiftool_ok(monkeypatch):
    monkeypatch.setattr(rec, "_run_exiftool_argfile",
                        lambda lines, timeout=60: subprocess.CompletedProcess(
                            ["exiftool"], 0, stdout="", stderr=""))


class TestConvertOne:
    def test_convert_writes_output_and_restamps(self, tmp_path, monkeypatch):
        src = _fake_jxl(tmp_path / "photo.jxl", size=5000)
        final = tmp_path / "out" / "photo.jxl"
        _mock_cjxl_writes(monkeypatch, out_size=3000)
        exif_calls = []
        monkeypatch.setattr(rec, "_run_exiftool_argfile",
                            lambda lines, timeout=60: (exif_calls.append(lines),
                                                       subprocess.CompletedProcess(
                                                           ["exiftool"], 0, "", ""))[1])
        _src, status, _f = rec.convert_one(src, final, final, "convert", False,
                                           "cjxl d=0.1 e=7", "", 0.1)
        assert status == "ok"
        assert rec._verify_jxl_integrity(final)
        lines = exif_calls[0]
        # Metadata copied from the source JXL...
        assert "-tagsfromfile" in lines and str(src) in lines
        assert "-xmp:all" in lines and "-exif:all" in lines
        # ...and the new parameters APPENDED to the chain, gen reconciled
        assert "-XMP-dc:Description=gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7" in lines

    def test_overwrite_status_when_final_exists(self, tmp_path, monkeypatch):
        src = _fake_jxl(tmp_path / "photo.jxl", size=5000)
        final = _fake_jxl(tmp_path / "out" / "photo.jxl", size=100)
        monkeypatch.setattr(rec, "OVERWRITE", True)  # never skip
        _mock_cjxl_writes(monkeypatch, out_size=3000)
        _mock_exiftool_ok(monkeypatch)
        _s, status, _f = rec.convert_one(src, final, final, "convert", False, "", "", 0.1)
        assert status == "overwrite"

    def test_keep_smaller_falls_back_to_copy(self, tmp_path, monkeypatch):
        src = _fake_jxl(tmp_path / "photo.jxl", size=3000)
        final = tmp_path / "out" / "photo.jxl"
        _mock_cjxl_writes(monkeypatch, out_size=5000)  # re-encode GREW the file
        _mock_exiftool_ok(monkeypatch)
        _s, status, _f = rec.convert_one(src, final, final, "convert", False, "", "", 0.1)
        assert status == "copied"
        assert final.read_bytes() == src.read_bytes(), (
            "keep-smaller must keep the SOURCE bytes, not the larger re-encode")

    def test_keep_smaller_disabled_keeps_reencode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rec, "KEEP_SMALLER", False)
        src = _fake_jxl(tmp_path / "photo.jxl", size=3000)
        final = tmp_path / "out" / "photo.jxl"
        _mock_cjxl_writes(monkeypatch, out_size=5000)
        _mock_exiftool_ok(monkeypatch)
        _s, status, _f = rec.convert_one(src, final, final, "convert", False, "", "", 0.1)
        assert status == "ok"
        assert final.stat().st_size == 5000

    def test_in_place_not_smaller_keeps_original(self, tmp_path, monkeypatch):
        src = _fake_jxl(tmp_path / "photo.jxl", size=3000)
        write = tmp_path / ("aaaa0000" * 4 + "_photo.jxl")
        before = src.read_bytes()
        _mock_cjxl_writes(monkeypatch, out_size=5000)
        _mock_exiftool_ok(monkeypatch)
        _s, status, _f = rec.convert_one(src, write, src, "convert", True, "", "", 0.1)
        assert status == "skipped"
        assert src.read_bytes() == before, "in-place original must be untouched"
        assert not write.exists(), "the re-encode temp must be discarded"

    def test_copy_action_is_verbatim(self, tmp_path, monkeypatch):
        src = _fake_jxl(tmp_path / "photo.jxl", size=3000, jbrd=True)
        final = tmp_path / "out" / "photo.jxl"
        _s, status, _f = rec.convert_one(src, final, final, "copy", False, "", "", None)
        assert status == "copied"
        assert final.read_bytes() == src.read_bytes()

    def test_copy_in_place_is_a_noop_skip(self, tmp_path):
        src = _fake_jxl(tmp_path / "photo.jxl")
        _s, status, _f = rec.convert_one(src, src, src, "copy", True, "", "", None)
        assert status == "skipped"

    def test_cjxl_failure_is_error(self, tmp_path, monkeypatch):
        src = _fake_jxl(tmp_path / "photo.jxl")
        final = tmp_path / "out" / "photo.jxl"
        _mock_cjxl_writes(monkeypatch, out_size=0, rc=1)
        _mock_exiftool_ok(monkeypatch)
        _s, status, _f = rec.convert_one(src, final, final, "convert", False, "", "", 0.1)
        assert status == "error"
        assert not final.exists()

    def test_metadata_failure_is_error_not_silent(self, tmp_path, monkeypatch):
        """A failed exiftool copy would drop the ICC/EXIF this tool exists to
        keep — it must fail the file, never wave it through."""
        src = _fake_jxl(tmp_path / "photo.jxl")
        final = tmp_path / "out" / "photo.jxl"
        _mock_cjxl_writes(monkeypatch, out_size=3000)
        monkeypatch.setattr(rec, "_run_exiftool_argfile",
                            lambda lines, timeout=60: subprocess.CompletedProcess(
                                ["exiftool"], 1, "", "boom"))
        _s, status, _f = rec.convert_one(src, final, final, "convert", False, "", "", 0.1)
        assert status == "error"

    def test_skip_existing_smart_sync(self, tmp_path, monkeypatch):
        src = _fake_jxl(tmp_path / "photo.jxl")
        final = _fake_jxl(tmp_path / "out" / "photo.jxl")
        # Output newer than source: smart sync skips.
        os.utime(src, (1000, 1000))
        os.utime(final, (2000, 2000))
        _s, status, _f = rec.convert_one(src, final, final, "convert", False, "", "", 0.1)
        assert status == "skipped"

    def test_skip_never_applies_in_place(self, tmp_path, monkeypatch):
        """In-place the output IS the input: mtimes always tie, and a 'skip'
        there would make mode 8 a no-op."""
        src = _fake_jxl(tmp_path / "photo.jxl", size=5000)
        write = tmp_path / "tmp_photo.jxl"
        _mock_cjxl_writes(monkeypatch, out_size=3000)
        _mock_exiftool_ok(monkeypatch)
        _s, status, _f = rec.convert_one(src, write, src, "convert", True, "", "", 0.1)
        assert status == "ok"


# ---------------------------------------------------------------------------
# resolve_output / finders
# ---------------------------------------------------------------------------

class TestResolveOutput:
    def test_mode3_subfolder(self, tmp_path):
        src = tmp_path / "session" / "photo.jxl"
        assert rec.resolve_output(src, 3, tmp_path) == (
            tmp_path / "session" / "JXL_recompressed" / "photo.jxl")

    def test_mode4_token_replace(self, tmp_path):
        src = tmp_path / "lib" / "16B_JXL" / "photo.jxl"
        assert rec.resolve_output(src, 4, tmp_path / "lib") == (
            tmp_path / "lib" / "16B_JXL_small" / "photo.jxl")

    def test_mode4_no_token_appends(self, tmp_path):
        src = tmp_path / "lib" / "photos" / "photo.jxl"
        assert rec.resolve_output(src, 4, tmp_path / "lib") == (
            tmp_path / "lib" / "photos_JXL_small" / "photo.jxl")

    def test_mode5_sibling(self, tmp_path):
        src = tmp_path / "lib" / "session" / "photo.jxl"
        assert rec.resolve_output(src, 5, tmp_path / "lib") == (
            tmp_path / "lib" / "JXL_recompressed" / "photo.jxl")

    def test_mode6_marker_anchor(self, tmp_path):
        src = tmp_path / "2025" / "_EXPORT" / "16B_JXL" / "photo.jxl"
        assert rec.resolve_output(src, 6, tmp_path / "2025") == (
            tmp_path / "2025" / "_EXPORT" / "16B_JXL_small" / "photo.jxl")

    def test_mode6_outside_marker_is_none(self, tmp_path):
        src = tmp_path / "2025" / "photos" / "photo.jxl"
        assert rec.resolve_output(src, 6, tmp_path / "2025") is None

    def test_mode7_subfolder_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rec, "EXPORT_JXL_SUBFOLDER", "16B_JXL")
        good = tmp_path / "X" / "_EXPORT" / "16B_JXL" / "photo.jxl"
        bad = tmp_path / "X" / "_EXPORT" / "sRGB" / "photo.jxl"
        assert rec.resolve_output(good, 7, tmp_path / "X") == (
            tmp_path / "X" / "_EXPORT" / "16B_JXL_small" / "photo.jxl")
        assert rec.resolve_output(bad, 7, tmp_path / "X") is None

    def test_mode8_in_place(self, tmp_path):
        src = tmp_path / "a" / "b" / "photo.jxl"
        assert rec.resolve_output(src, 8, tmp_path) == src


class TestFinders:
    def test_recursive_scan_skips_own_outputs(self, tmp_path):
        _fake_jxl(tmp_path / "in" / "a.jxl")
        _fake_jxl(tmp_path / "in" / "sub" / "b.jxl")
        _fake_jxl(tmp_path / "in" / "recompressed_jxl" / "c.jxl")
        _fake_jxl(tmp_path / "in" / "16B_JXL_small" / "d.jxl")
        found = rec.find_jxls_recursive(tmp_path / "in")
        names = sorted(f.name for f in found)
        assert names == ["a.jxl", "b.jxl"]

    def test_root_named_output_folder_is_scanned(self, tmp_path):
        # Pointing the run AT an own-output folder (e.g. to compress it
        # again) is legitimate: only DESCENDANT output folders are skipped.
        root = tmp_path / "recompressed_jxl"
        _fake_jxl(root / "here.jxl")
        _fake_jxl(root / "sub" / "jxl_small" / "nested.jxl")
        found = rec.find_jxls_recursive(root)
        assert [f.name for f in found] == ["here.jxl"]

    def test_mode6_requires_marker(self, tmp_path):
        _fake_jxl(tmp_path / "in" / "_EXPORT" / "16B_JXL" / "a.jxl")
        _fake_jxl(tmp_path / "in" / "elsewhere" / "b.jxl")
        found = rec.find_jxls_mode6(tmp_path / "in")
        assert [f.name for f in found] == ["a.jxl"]


# ---------------------------------------------------------------------------
# Delete gates — fail CLOSED on every check
# ---------------------------------------------------------------------------

def _gate_item(src, final, action="convert", in_place=False):
    return {"src": src, "final": final, "action": action, "in_place": in_place,
            "src_d": 0.1, "desc": "", "software": ""}


class TestDeleteGate:
    @pytest.fixture(autouse=True)
    def _no_mpg_markers(self, monkeypatch):
        # Group-marker reading is exercised elsewhere; these tests target the
        # per-file gates, so stub it as "read OK, no markers". The real reader
        # on these stub files now fails closed (no deletion at all).
        monkeypatch.setattr(rec, "_read_mpg_markers", lambda paths: ({}, True))

    def test_converted_source_deleted_after_integrity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rec, "DELETE_SOURCE", True)
        src = _fake_jxl(tmp_path / "a.jxl")
        final = _fake_jxl(tmp_path / "out" / "a.jxl")
        it = _gate_item(src, final)
        rec._delete_gate([it], {str(src): ("ok", str(final))}, {str(src)})
        assert not src.exists()

    def test_corrupt_output_keeps_source(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rec, "DELETE_SOURCE", True)
        src = _fake_jxl(tmp_path / "a.jxl")
        final = tmp_path / "out" / "a.jxl"
        final.parent.mkdir(parents=True)
        final.write_bytes(b"\xff\x0a" + b"\x00" * 50)  # bare codestream: refused
        it = _gate_item(src, final)
        rec._delete_gate([it], {str(src): ("ok", str(final))}, {str(src)})
        assert src.exists()
        assert rec._delete_stats["kept"] == 1

    def test_copy_requires_md5_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rec, "DELETE_SOURCE", True)
        src = _fake_jxl(tmp_path / "a.jxl", size=1024)
        good = tmp_path / "out" / "a.jxl"
        good.parent.mkdir(parents=True)
        good.write_bytes(src.read_bytes())
        it = _gate_item(src, good, action="copy")
        rec._delete_gate([it], {str(src): ("copied", str(good))}, {str(src)})
        assert not src.exists()

    def test_copy_md5_mismatch_keeps(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rec, "DELETE_SOURCE", True)
        src = _fake_jxl(tmp_path / "a.jxl", size=1024)
        bad = _fake_jxl(tmp_path / "out" / "a.jxl", size=2048)  # different bytes
        it = _gate_item(src, bad, action="copy")
        rec._delete_gate([it], {str(src): ("copied", str(bad))}, {str(src)})
        assert src.exists(), "a copy whose bytes differ must never justify a delete"

    def test_not_processed_this_run_is_not_deleted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rec, "DELETE_SOURCE", True)
        src = _fake_jxl(tmp_path / "a.jxl")
        final = _fake_jxl(tmp_path / "out" / "a.jxl")
        it = _gate_item(src, final)
        rec._delete_gate([it], {str(src): ("error", str(final))}, set())
        assert src.exists()

    def test_delete_skipped_alone_is_inert(self, tmp_path, monkeypatch):
        """C2: --delete-skipped without --delete-source must delete NOTHING.

        Armed alone it used to "finish interrupted archives" silently — no
        confirmation, no provenance check — and a same-named output from a
        DIFFERENT photo was enough to get the only copy of a source
        destroyed, with exit 0. Now it is inert, like in the other scripts;
        finishing an interrupted archive requires --delete-source (which
        brings the confirmation and the provenance gates with it).
        """
        monkeypatch.setattr(rec, "DELETE_SOURCE", False)
        monkeypatch.setattr(rec, "DELETE_SKIPPED", True)
        src_skip = _fake_jxl(tmp_path / "skip.jxl")
        fin_skip = _fake_jxl(tmp_path / "out" / "skip.jxl")
        src_ok = _fake_jxl(tmp_path / "ok.jxl")
        fin_ok = _fake_jxl(tmp_path / "out" / "ok.jxl")
        items = [_gate_item(src_skip, fin_skip), _gate_item(src_ok, fin_ok)]
        results = {str(src_skip): ("skipped", str(fin_skip)),
                   str(src_ok): ("ok", str(fin_ok))}
        rec._delete_gate(items, results, {str(src_ok)})
        assert src_skip.exists(), "--delete-skipped alone deleted a source"
        assert src_ok.exists(), "--delete-skipped alone deleted a source"

    def test_in_place_items_are_never_in_the_gate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rec, "DELETE_SOURCE", True)
        src = _fake_jxl(tmp_path / "a.jxl")
        it = _gate_item(src, src, in_place=True)
        rec._delete_gate([it], {str(src): ("ok", str(src))}, {str(src)})
        assert src.exists()


# ---------------------------------------------------------------------------
# Batch encode-param reading (exiftool mocked)
# ---------------------------------------------------------------------------

class TestReadEncodeParamsBatch:
    def test_parses_json_and_normcases(self, tmp_path, monkeypatch):
        a = _fake_jxl(tmp_path / "A.jxl")
        b = _fake_jxl(tmp_path / "B.jxl")
        payload = [
            {"SourceFile": str(a), "Description": "cjxl d=0.1 e=7"},
            {"SourceFile": str(b), "Software": "C1 | cjxl d=0.05 e=9"},
        ]

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload),
                                               stderr="")
        monkeypatch.setattr(rec.subprocess, "run", fake_run)
        info = rec._read_encode_params_batch([a, b])
        assert info[str(a)]["params"] == (0.1, 7)
        assert info[str(b)]["params"] == (0.05, 9)

    def test_unreadable_file_is_unknown(self, tmp_path, monkeypatch):
        a = _fake_jxl(tmp_path / "A.jxl")
        monkeypatch.setattr(rec.subprocess, "run",
                            lambda cmd, **kw: subprocess.CompletedProcess(
                                cmd, 1, stdout="", stderr="io error"))
        info = rec._read_encode_params_batch([a])
        assert info[str(a)]["params"] is None


# ---------------------------------------------------------------------------
# Wrapper wiring (jxl_photo.py): the new direction must reach the right child
# with the right flags
# ---------------------------------------------------------------------------

import jxl_photo as wp


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


class TestWrapperWiring:
    def test_session_choices_accept_the_new_type(self):
        assert "jxl_recompress" in wp._SESSION_CHOICES["last_conversion_type"]

    def test_export_folder_name(self):
        assert wp._export_folder_name("jxl", "jxl") == "16B_JXL_small"

    def test_manifest_cmd_builder(self):
        menu = _menu()
        cmd = menu._build_manifest_entry_cmd(
            script="jxl_recompressor.py",
            source=str(Path("F:/lib")), dest_path="", mode=3,
            origin="jxl", dest="jxl", workers=8,
            workflow={"distance": 1.0, "effort": 7, "mode_config": {},
                      "conversion_type": "jxl_recompress"},
            advanced={"on_downgrade": "copy", "delete_source": True,
                      "verify_roundtrip": True, "delete_skipped": True,
                      "provenance": "content"},
        )
        assert cmd[1].endswith("jxl_recompressor.py")
        for flag in ("--distance", "--effort", "--on-downgrade", "--delete-source",
                     "--delete-confirm-off", "--verify-roundtrip",
                     "--delete-skipped", "--provenance", "--summary-json"):
            assert flag in cmd, flag
        assert cmd[cmd.index("--on-downgrade") + 1] == "copy"
        assert cmd[cmd.index("--distance") + 1] == "1.0"
        # Not a transcoder run: none of its flags may leak in
        for foreign in ("--force-transcode", "--force-convert", "--from-jxl",
                        "--quality", "--format", "--no-md5"):
            assert foreign not in cmd, foreign

    def test_manifest_cmd_builder_minimal(self):
        """No advanced options: only the mandatory flags go out."""
        menu = _menu()
        cmd = menu._build_manifest_entry_cmd(
            script="jxl_recompressor.py",
            source=str(Path("F:/lib")), dest_path="", mode=0,
            origin="jxl", dest="jxl", workers=4,
            workflow={"mode_config": {}, "conversion_type": "jxl_recompress"},
            advanced={},
        )
        assert "--on-downgrade" not in cmd   # child default ("ask") applies
        assert "--delete-source" not in cmd

    def test_describe_session_shows_distance_not_quality(self):
        menu = _menu()
        session = {"last_output_mode": "5", "last_input_dir": "F:/lib",
                   "last_workers": 8, "last_origin_format": "jxl",
                   "last_dest_format": "jxl",
                   "last_conversion_type": "jxl_recompress",
                   "last_distance": 2.0, "last_quality": 95}
        desc = menu._describe_session(session)
        assert "d=2" in desc
        assert "q=" not in desc

    def test_collision_scan_knows_the_recompressor(self, tmp_path, monkeypatch):
        """_manifest_output_collisions must resolve outputs with the
        recompressor's own rules: two same-named JXLs in one flat Destination
        are a collision it has to SEE."""
        menu = _menu()
        import jxl_recompressor as _r
        monkeypatch.setattr(_r, "logger", _QuietLogger())
        fa = _fake_jxl(tmp_path / "a" / "photo.jxl")
        fb = _fake_jxl(tmp_path / "b" / "photo.jxl")
        entries = [(str(tmp_path / "a"), str(tmp_path / "out"), 0),
                   (str(tmp_path / "b"), str(tmp_path / "out"), 0)]
        collisions = menu._manifest_output_collisions(
            entries, {".jxl"}, origin="jxl", dest="jxl")
        assert collisions, "two entries collapsing onto out/photo.jxl were missed"


class _QuietLogger:
    """The collision scan warns via the child's logger; keep the output clean."""
    def __getattr__(self, _name):
        return lambda *a, **k: None
