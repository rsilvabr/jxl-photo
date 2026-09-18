#!/usr/bin/env python3
"""Tests for the generation counter in the encode record (v2.1.0).

The record is append-only: "caption | gen=N | cjxl d=X e=Y | cjxl d=... e=..."
gen=N leads the machine block and is DERIVED (max of stored token and lossy
chain length), never incremented. These tests pin:

- the escalation hole: d_new > d_old passes every step of a lossy chain, so
  only the gen count lets --on-regeneration fire (measured ~1 dB per lossy
  generation regardless of step size);
- reconciliation (max), legacy fields (chain, no gen=), d=0 not counting;
- the recompressor appending instead of replacing, and the encoder appending
  even at identical d/e (no dedup);
- caption preservation, orphan gen= handling, --encode-tag off stripping,
  and the writer<->reader round-trip (same anchoring on both sides).

Every test here fails against the pre-change code (the helpers did not exist
/ the recompressor replaced the chain / the encoder deduplicated).
"""

import logging
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_recompressor as rec
import jxl_tiff_encoder as enc


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(rec, "CJXL_DISTANCE", 1.0)
    monkeypatch.setattr(rec, "CJXL_EFFORT", 7)
    monkeypatch.setattr(rec, "ENCODE_TAG_MODE", "xmp")
    monkeypatch.setattr(rec, "ON_DOWNGRADE", "ask")
    monkeypatch.setattr(rec, "ON_REGENERATION", "ask")
    monkeypatch.setattr(rec, "ON_UNKNOWN", "convert")
    monkeypatch.setattr(rec, "JBRD_POLICY", "copy")
    monkeypatch.setattr(rec, "_gen_divergence_logged", False)
    monkeypatch.setattr(enc, "CJXL_DISTANCE", 0.1)
    monkeypatch.setattr(enc, "CJXL_EFFORT", 7)
    monkeypatch.setattr(enc, "ENCODE_TAG_MODE", "xmp")
    monkeypatch.setattr(enc, "_gen_divergence_logged", False)
    yield


# ---------------------------------------------------------------------------
# The escalation hole: three runs, each "ok" step-by-step, three generations
# ---------------------------------------------------------------------------

class TestRegenerationGuard:
    def test_three_run_escalation_is_caught(self):
        # Run 1: fresh lossy encode at d=0.1 — gen 1.
        field, _, _, _ = enc._append_encode_entry("", 0.1, 7)
        assert field == "gen=1 | cjxl d=0.1 e=7"
        # Run 2: d=1.0 > d=0.1 classifies "ok" (genuinely smaller target).
        # The guard does NOT fire: every lossy file this toolkit's encoder
        # produces is born at gen=1, so guarding at gen>=1 would turn the
        # recompressor's main use case (encoder preview -> final archive)
        # into an "ask" that silently skips everything headless.
        gen, _, _ = rec._reconcile_gen(field)
        assert gen == 1
        assert rec._regeneration_action(gen, 1.0) is None
        # Run 3: after the restamp the chain carries both entries, gen=2,
        # and the guard fires for any further lossy request (d=2.0).
        field2, _, _, _ = rec._append_encode_entry(field, 1.0, 7)
        assert field2 == "gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7"
        gen3, _, _ = rec._reconcile_gen(field2)
        assert gen3 == 2
        assert rec._regeneration_action(gen3, 2.0) == "ask"

    def test_policy_values(self, monkeypatch):
        for policy in ("copy", "skip", "convert"):
            monkeypatch.setattr(rec, "ON_REGENERATION", policy)
            assert rec._regeneration_action(3, 1.0) == policy

    def test_first_lossy_generation_does_not_fire(self):
        assert rec._regeneration_action(0, 1.0) is None

    def test_lossless_request_does_not_fire(self):
        # d=0 adds no generation (the entry is appended but does not count),
        # so the guard stays quiet even on a gen>=1 file.
        assert rec._regeneration_action(2, 0.0) is None

    def test_more_conservative_ordering(self):
        # skip > copy > ask > convert, whichever side it comes from
        assert rec._more_conservative("convert", "ask") == "ask"
        assert rec._more_conservative("ask", "convert") == "ask"
        assert rec._more_conservative("copy", "ask") == "copy"
        assert rec._more_conservative("skip", "copy") == "skip"
        assert rec._more_conservative("convert", "convert") == "convert"

    def test_unattended_ask_skips(self):
        """A non-interactive run (wrapper, scheduled task) fails CLOSED."""
        items = [{"action": "ask", "category": "regeneration",
                  "src": Path("a.jxl"), "reason": "gen=1 already"}]
        rec._ask_batch_resolution(items)  # pytest's stdin is not a TTY
        assert items[0]["action"] == "skip"


# ---------------------------------------------------------------------------
# gen is derived, never incremented
# ---------------------------------------------------------------------------

class TestReconcileGen:
    def test_gen_equals_lossy_count_after_fresh_encode(self):
        field, _, _, _ = enc._append_encode_entry("", 0.1, 7)
        assert rec._reconcile_gen(field) == (1, 1, 1)

    def test_gen_after_recompress(self):
        field, _, _, _ = enc._append_encode_entry("", 0.1, 7)
        field, _, _, _ = rec._append_encode_entry(field, 1.0, 7)
        assert rec._reconcile_gen(field) == (2, 2, 2)

    def test_gen_after_decode_and_reencode_cycle(self):
        # The decoder carries the JXL's Description into the TIFF; the
        # encoder appends onto it on the way back.
        field, _, _, _ = enc._append_encode_entry("", 0.1, 7)
        field, _, _, _ = rec._append_encode_entry(field, 1.0, 7)
        field, _, _, _ = enc._append_encode_entry(field, 0.5, 7)
        assert field == "gen=3 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7 | cjxl d=0.5 e=7"
        assert rec._reconcile_gen(field) == (3, 3, 3)

    def test_stored_higher_than_count_wins(self):
        # Entries were removed by hand: the stored gen is the better number.
        gen, stored, counted = rec._reconcile_gen("gen=5 | cjxl d=0.1 e=7")
        assert (gen, stored, counted) == (5, 5, 1)

    def test_count_higher_than_stored_wins(self):
        # Entries were added without updating gen: the count is better.
        gen, stored, counted = rec._reconcile_gen(
            "gen=1 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7")
        assert (gen, stored, counted) == (2, 1, 2)
        # ...and the next write self-corrects the stored token.
        field, _, _, _ = rec._append_encode_entry(
            "gen=1 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7", 2.0, 7)
        assert field.startswith("gen=3 |")

    def test_divergence_logged_once_per_run(self, monkeypatch, caplog):
        monkeypatch.setattr(rec, "_gen_divergence_logged", False)
        with caplog.at_level(logging.INFO):
            rec._log_gen_notes_once(5, 1)
            rec._log_gen_notes_once(4, 2)
        assert caplog.text.count("disagrees") == 1

    def test_legacy_field_no_gen_no_warning(self, caplog):
        # Chains written before gen= existed: count rules, no special case.
        legacy = "My caption | cjxl d=0.1 e=7 | cjxl d=1.0 e=7"
        assert rec._reconcile_gen(legacy) == (2, 0, 2)
        with caplog.at_level(logging.INFO):
            rec._log_gen_notes_once(0, 2)   # stored == 0: never a divergence
        assert caplog.text == ""

    def test_malformed_entry_does_not_stop_the_count(self):
        gen, _, counted = rec._reconcile_gen(
            "cjxl d=1.2.3 e=7 | cjxl d=0.5 e=7")
        assert (gen, counted) == (1, 1)

    def test_unparseable_gen_is_absent(self):
        gen, stored, counted = rec._reconcile_gen("gen=abc | cjxl d=0.5 e=7")
        assert (gen, stored, counted) == (1, 0, 1)

    def test_gen_in_running_text_is_not_a_token(self):
        # Anchored read: "Project gen=3 phase 2" is a caption, not a token.
        gen, stored, counted = rec._reconcile_gen(
            "Project gen=3 phase 2 | cjxl d=0.5 e=7")
        assert (gen, stored, counted) == (1, 0, 1)


# ---------------------------------------------------------------------------
# d=0 appends but does not count
# ---------------------------------------------------------------------------

class TestLosslessEntries:
    def test_d0_appends_without_incrementing(self):
        field, _, _, _ = rec._append_encode_entry("gen=1 | cjxl d=0.5 e=7", 0, 9)
        assert field == "gen=1 | cjxl d=0.5 e=7 | cjxl d=0 e=9"
        assert rec._reconcile_gen(field) == (1, 1, 1)

    def test_lossless_only_chain_is_gen_0(self):
        field, _, _, _ = enc._append_encode_entry("", 0, 9)
        assert field == "gen=0 | cjxl d=0 e=9"


# ---------------------------------------------------------------------------
# Append, never replace; the encoder never dedups
# ---------------------------------------------------------------------------

class TestAppendOnly:
    def test_recompressor_appends_and_keeps_the_old_chain(self):
        lines = rec._restamp_args("gen=1 | cjxl d=0.1 e=7", "")
        assert lines == [
            "-XMP-dc:Description=gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7"]

    def test_encoder_reencode_at_identical_params_appends(self):
        # The old dedup branch made a second d=0.1 e=7 encode a no-op in the
        # record — two generations counted as one. Every encode appends now.
        field, _, _, _ = enc._append_encode_entry("gen=1 | cjxl d=0.1 e=7", 0.1, 7)
        assert field == "gen=2 | cjxl d=0.1 e=7 | cjxl d=0.1 e=7"

    def test_encoder_argfile_appends_at_identical_params(self, monkeypatch, tmp_path):
        argfile = _encoder_argfile(monkeypatch, tmp_path,
                                   existing_desc="cjxl d=0.1 e=7", distance=0.1)
        assert "gen=2 | cjxl d=0.1 e=7 | cjxl d=0.1 e=7" in argfile


# ---------------------------------------------------------------------------
# User text and orphaned tokens
# ---------------------------------------------------------------------------

class TestFieldPreservation:
    def test_caption_stays_first_through_encode_and_recompress(self):
        field, _, _, _ = enc._append_encode_entry("Summer 2024", 0.1, 7)
        assert field == "Summer 2024 | gen=1 | cjxl d=0.1 e=7"
        field, _, _, _ = rec._append_encode_entry(field, 1.0, 7)
        assert field == "Summer 2024 | gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7"

    def test_orphan_gen_at_tail_is_stripped(self):
        assert rec._strip_encode_params("My caption | gen=2") == ("My caption", 1)

    def test_orphan_gen_before_a_caption_stays(self):
        # "gen=2" followed by what is clearly a caption is left alone.
        assert rec._strip_encode_params("gen=2 | My caption") == ("gen=2 | My caption", 0)

    def test_gen_inside_running_text_stays(self):
        assert rec._strip_encode_params("Project gen=3 phase 2") == ("Project gen=3 phase 2", 0)

    def test_orphan_strip_is_logged(self, caplog):
        with caplog.at_level(logging.WARNING):
            lines = rec._restamp_args("My caption | gen=2", "", label="photo.jxl")
        assert "photo.jxl" in caplog.text and "orphaned gen=" in caplog.text
        # The orphan's claim (gen=2) is still honoured — max() keeps the
        # larger, safer value even though the token itself was stripped.
        assert "-XMP-dc:Description=My caption | gen=2 | cjxl d=1.0 e=7" in lines


# ---------------------------------------------------------------------------
# Verbatim-copy paths carry the field byte-for-byte
# ---------------------------------------------------------------------------

class TestVerbatimCopy:
    def test_copy_never_touches_metadata(self, tmp_path, monkeypatch):
        src = tmp_path / "photo.jxl"
        src.write_bytes(b"\x00\x00\x00\x0cJXL \x0d\x0a\x87\x0a"
                        + (1016).to_bytes(4, "big") + b"jxlc" + b"\x00" * 1008)
        final = tmp_path / "out" / "photo.jxl"
        monkeypatch.setattr(rec, "_run_exiftool_argfile",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("copy must not restamp")))
        _s, status, _f = rec.convert_one(src, final, final, "copy", False,
                                         "gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7",
                                         "", 1.0)
        assert status == "copied"
        assert final.read_bytes() == src.read_bytes()


# ---------------------------------------------------------------------------
# --encode-tag off / software
# ---------------------------------------------------------------------------

class TestEncodeTagModes:
    def test_off_strips_gen_and_chain_together(self, monkeypatch):
        monkeypatch.setattr(rec, "ENCODE_TAG_MODE", "off")
        lines = rec._restamp_args("Notes | gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7", "")
        assert lines == ["-XMP-dc:Description=Notes"]

    def test_encoder_off_strips_the_record(self, monkeypatch, tmp_path):
        argfile = _encoder_argfile(
            monkeypatch, tmp_path, mode="off",
            existing_desc="My caption | gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7")
        assert "-xmp-dc:Description=My caption" in argfile
        assert "gen=" not in argfile and "cjxl d=" not in argfile

    def test_encoder_off_leaves_clean_fields_alone(self, monkeypatch, tmp_path):
        argfile = _encoder_argfile(monkeypatch, tmp_path, mode="off",
                                   existing_desc="Just a caption")
        assert "-xmp-dc:Description" not in argfile
        assert "-Software" not in argfile

    def test_software_mode_gets_the_gen_token(self, monkeypatch):
        monkeypatch.setattr(rec, "ENCODE_TAG_MODE", "software")
        lines = rec._restamp_args("", "Capture One 23 | cjxl d=0.1 e=7")
        assert "-Software=Capture One 23 | gen=2 | cjxl d=0.1 e=7 | cjxl d=1.0 e=7" in lines

    def test_encoder_software_mode_gets_the_gen_token(self, monkeypatch, tmp_path):
        argfile = _encoder_argfile(monkeypatch, tmp_path, mode="software",
                                   sw_stdout="Capture One 23\n")
        assert "-Software=Capture One 23 | gen=1 | cjxl d=0.1 e=7" in argfile


# ---------------------------------------------------------------------------
# Writer <-> reader round-trip (same anchoring on both sides), property-style
# ---------------------------------------------------------------------------

class TestRoundTrip:
    @pytest.mark.parametrize("caption", [
        "", "My caption", "Project gen=3 phase 2", "a | b | c",
    ])
    @pytest.mark.parametrize("distances", [
        [0.1], [0.1, 1.0], [0.1, 1.0, 2.0], [0.0, 0.1], [0.5, 0.0, 1.5],
    ])
    def test_writer_output_reads_back_with_the_same_gen(self, caption, distances):
        field = caption
        for d in distances:
            field, _, _, _ = rec._append_encode_entry(field, d, 7)
        gen, stored, counted = rec._reconcile_gen(field)
        lossy = sum(1 for d in distances if d > 0)
        assert gen == stored == counted == lossy
        # The encoder's copy reads it identically (parity, enforced by AST too)
        assert enc._reconcile_gen(field) == (gen, stored, counted)
        # User text survives intact, at the head of the field
        if caption:
            assert field.startswith(caption + " | gen=")
        # Re-stripping the block returns exactly the caption
        assert rec._strip_encode_params(field) == (caption, 0)


# ---------------------------------------------------------------------------
# Encoder argfile builder (exiftool reads mocked)
# ---------------------------------------------------------------------------

def _encoder_argfile(monkeypatch, tmp_path, existing_desc="", sw_stdout="",
                     mode="xmp", distance=0.1, strip=False) -> str:
    """Run build_metadata_injection_args with all exiftool reads mocked and
    return the written argfile's text."""
    monkeypatch.setattr(enc, "ENCODE_TAG_MODE", mode)
    monkeypatch.setattr(enc, "CJXL_DISTANCE", distance)
    monkeypatch.setattr(enc, "CJXL_EFFORT", 7)
    monkeypatch.setattr(enc, "EMBED_ICC_IN_JXL", False)
    monkeypatch.setattr(enc, "CLEANUP_XMP_ICC_MARKER", False)
    monkeypatch.setattr(enc, "read_existing_description", lambda p: existing_desc)
    monkeypatch.setattr(enc, "read_existing_relation", lambda p: [])
    monkeypatch.setattr(enc, "read_existing_creator_tool", lambda p: "")
    monkeypatch.setattr(enc.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(
                            cmd, 0, stdout=sw_stdout, stderr=""))
    xmp = tmp_path / "src.xmp"
    xmp.write_text("<xmp/>", encoding="utf-8")
    tiff = tmp_path / "src.tif"
    tiff.write_bytes(b"II")
    arg = enc.build_metadata_injection_args(
        tiff, tmp_path / "out.jxl", tmp_path, None, None, xmp,
        strip_metadata=strip)
    return Path(arg).read_text(encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# --strip must respect --encode-tag off: "no metadata" means NO metadata,
# and the encode record is metadata. (Fixed separately from the lineage work.)
# ---------------------------------------------------------------------------

class TestStripMetadataRespectsOff:
    def test_strip_writes_the_record_by_default(self, monkeypatch, tmp_path):
        argfile = _encoder_argfile(monkeypatch, tmp_path, strip=True)
        assert "gen=1 | cjxl d=0.1 e=7" in argfile

    def test_strip_with_off_writes_nothing(self, monkeypatch, tmp_path):
        argfile = _encoder_argfile(monkeypatch, tmp_path, mode="off", strip=True)
        assert "cjxl d=" not in argfile
        assert "gen=" not in argfile
        # ...but the strip itself still happens
        assert "-xmp:all=" in argfile and "-exif:all=" in argfile
