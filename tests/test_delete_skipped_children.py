#!/usr/bin/env python3
"""--delete-skipped in the decoder and the transcoder.

Same idea as the encoder's, three very different guarantees behind it:

  decoder (JXL -> TIFF)      structural check only. This script has no
                             --verify-roundtrip: its output is the product of a
                             pipeline with several knobs (--depth,
                             --depth-policy, --matrix/--basic/--none,
                             --target-icc, the appended JPEG preview page), so
                             re-deriving it to compare would reject good
                             archives whenever the settings differ.
  transcoder LOSSLESS        checksums.md5 holds the SOURCE's md5 keyed by the
  (JPEG <-> JXL)             OUTPUT's name, so provenance is PROVEN, not
                             estimated — stronger than any pixel comparison and
                             cheaper (no decode).
  transcoder LOSSY           structural check only, and nothing can do better:
  (JXL -> JPEG/PNG, ...)     no checksum is stored and the output cannot
                             reproduce the source. The run warns and the
                             wrapper charges its own confirmation.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_photo as wp
import jxl_tiff_decoder as dec

REPO = Path(__file__).resolve().parent.parent

STATUS = {k: True for k in
          ("cjxl", "djxl", "exiftool", "magick", "tifffile", "pillow", "imagecodecs")}


@pytest.fixture
def menu(tmp_path, monkeypatch):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(wp.InteractiveMenu, "_stream_child",
                        lambda self, cmd, idle_timeout=3600: (calls.append(list(map(str, cmd))), 0)[1])
    return calls


def _out(capsys) -> str:
    return " ".join(capsys.readouterr().out.split())


def _tiff(path: Path, value: int = 1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((16, 16, 3), value, np.uint16),
                     photometric="rgb")


def _jxl_stub(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 32)


def _make_newer(target: Path, than: Path):
    import os
    stamp = than.stat().st_mtime + 100
    os.utime(target, (stamp, stamp))


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

def _dec_run(tmp_path, monkeypatch, *, delete_skipped, integrity=True,
             staging=None, incomplete=False):
    """One JXL whose TIFF already exists, so the group reports SKIP."""
    import os
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "out" / "photo.tif"
    final.parent.mkdir()
    _tiff(final)
    _make_newer(final, src)

    dec.setup_logger()
    monkeypatch.setattr(dec, "DELETE_SOURCE", True)
    monkeypatch.setattr(dec, "DELETE_SKIPPED", delete_skipped)
    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    monkeypatch.setattr(dec, "TEMP2_DIR", staging)
    monkeypatch.setattr(dec, "_verify_tiff_integrity", lambda p: integrity)
    monkeypatch.setattr(dec, "_incomplete_groups",
                        {os.path.normcase(str(src))} if incomplete else set())

    task = {"type": "multi", "main_jxl": src,
            "entries": [(src, 0, False, False, 0, False, None)],
            "ignored_thumbs": [], "final_tiff": final}
    dec.process_group([task], 1, 3)
    return src


def test_decoder_keeps_skipped_source_by_default(tmp_path, monkeypatch):
    assert _dec_run(tmp_path, monkeypatch, delete_skipped=False).exists()


def test_decoder_delete_skipped_removes_it(tmp_path, monkeypatch):
    assert not _dec_run(tmp_path, monkeypatch, delete_skipped=True).exists()


def test_decoder_delete_skipped_needs_the_integrity_check(tmp_path, monkeypatch):
    assert _dec_run(tmp_path, monkeypatch, delete_skipped=True,
                    integrity=False).exists()


def test_decoder_delete_skipped_works_with_staging(tmp_path, monkeypatch):
    """The same trap as the encoder: a skipped group is never in moved_finals,
    so without an explicit exemption the flag silently deletes nothing."""
    staging = tmp_path / "stg"
    staging.mkdir()
    assert not _dec_run(tmp_path, monkeypatch, delete_skipped=True,
                        staging=str(staging)).exists()


def test_decoder_delete_skipped_still_blocked_by_incomplete_group(tmp_path, monkeypatch):
    assert _dec_run(tmp_path, monkeypatch, delete_skipped=True,
                    incomplete=True).exists()


def test_decoder_would_skip_matches_the_real_decision(tmp_path, monkeypatch):
    src = tmp_path / "a.jxl"
    _jxl_stub(src)
    final = tmp_path / "a.tif"
    _tiff(final)
    entries = [(src, 0, False, False, 0, False, None)]

    monkeypatch.setattr(dec, "OVERWRITE", "smart")
    _make_newer(final, src)
    assert dec._would_skip_group(entries, final) is True
    _make_newer(src, final)
    assert dec._would_skip_group(entries, final) is False
    final.unlink()
    assert dec._would_skip_group(entries, final) is False


# ---------------------------------------------------------------------------
# Transcoder — lossless, where provenance can be PROVEN
# ---------------------------------------------------------------------------

def _tr_run(tmp_path, monkeypatch, *, delete_skipped, stored="match",
            require_md5=True, integrity=True):
    """One JPEG whose JXL already exists, so it reports SKIP.

    stored: "match" | "wrong" | None (no checksums.md5 entry at all)
    """
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8" + b"payload-of-the-real-source" + b"\xff\xd9")
    final = tmp_path / "photo.jxl"
    final.write_bytes(b"archived jxl")
    _make_newer(final, src)

    if stored is not None:
        digest = tr.md5_of_file(src) if stored == "match" else "0" * 32
        (tmp_path / "checksums.md5").write_text(f"{digest}  {final.name}\n",
                                                encoding="utf-8")

    tr.setup_logger()
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "DELETE_SKIPPED", delete_skipped)
    monkeypatch.setattr(tr, "DELETE_SOURCE_REQUIRE_MD5", require_md5)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: integrity)
    monkeypatch.setattr(tr, "has_jbrd_box", lambda p: True)
    tr.process_group_transcode([(src, final)], 1, decode=False, verify=False,
                               mode=3, reconvert_val=False, smart=True)
    return src


def test_transcoder_keeps_skipped_source_by_default(tmp_path, monkeypatch):
    assert _tr_run(tmp_path, monkeypatch, delete_skipped=False).exists()


def test_transcoder_rejects_a_checksum_mismatch(tmp_path, monkeypatch):
    """checksums.md5 holds the SOURCE's hash keyed by the OUTPUT's name, so a
    mismatch PROVES the archived JXL did not come from this file."""
    assert _tr_run(tmp_path, monkeypatch, delete_skipped=True,
                   stored="wrong").exists()


def test_transcoder_accepts_proven_provenance(tmp_path, monkeypatch):
    assert not _tr_run(tmp_path, monkeypatch, delete_skipped=True,
                       stored="match").exists()


def test_transcoder_without_a_checksum_keeps_by_default(tmp_path, monkeypatch):
    """No stored hash means no provenance at all; the default refuses."""
    assert _tr_run(tmp_path, monkeypatch, delete_skipped=True,
                   stored=None).exists()


def test_transcoder_without_a_checksum_can_be_opted_out(tmp_path, monkeypatch, caplog):
    """DELETE_SOURCE_REQUIRE_MD5=False is an explicit opt-out — and it warns."""
    tr.logger.propagate = True
    with caplog.at_level("WARNING", logger="jxl_toolkit"):
        src = _tr_run(tmp_path, monkeypatch, delete_skipped=True,
                      stored=None, require_md5=False)
    assert not src.exists()


def test_transcoder_still_needs_integrity(tmp_path, monkeypatch):
    assert _tr_run(tmp_path, monkeypatch, delete_skipped=True,
                   stored="match", integrity=False).exists()


# ---------------------------------------------------------------------------
# Transcoder — lossy, where nothing can prove anything
# ---------------------------------------------------------------------------

def _tr_lossy(tmp_path, monkeypatch, *, delete_skipped, integrity=True):
    src = tmp_path / "photo.jxl"
    _jxl_stub(src)
    final = tmp_path / "photo.jpg"
    final.write_bytes(b"\xff\xd8\xff\xd9")
    _make_newer(final, src)

    tr.setup_logger()
    monkeypatch.setattr(tr, "DELETE_SOURCE", True)
    monkeypatch.setattr(tr, "DELETE_SKIPPED", delete_skipped)
    # cmd_convert charges its own HHMM token now that deletion is not mode-8
    # only. The wrapper suppresses it with --delete-confirm-off after asking;
    # here it would just block on stdin.
    monkeypatch.setattr(tr, "DELETE_CONFIRM", False)
    monkeypatch.setattr(tr, "TEMP2_DIR", None)
    monkeypatch.setattr(tr, "_verify_file_integrity", lambda p: integrity)
    monkeypatch.setattr(tr, "process_group_convert",
                        lambda pairs, *a, **k: ([(str(s), "skipped", str(o), None)
                                                 for s, o in pairs], set()))
    args = tr.build_parser().parse_args(
        [str(tmp_path), "--force-convert", "--decode", "--mode", "3"])
    tr.cmd_convert(args, from_jxl=True)
    return src


def test_transcoder_lossy_keeps_by_default(tmp_path, monkeypatch):
    assert _tr_lossy(tmp_path, monkeypatch, delete_skipped=False).exists()


def test_transcoder_lossy_delete_skipped_is_structural_only(tmp_path, monkeypatch):
    assert not _tr_lossy(tmp_path, monkeypatch, delete_skipped=True).exists()


def test_transcoder_lossy_honours_the_integrity_check(tmp_path, monkeypatch):
    """The structural check is the ONLY gate here, so it had better hold."""
    assert _tr_lossy(tmp_path, monkeypatch, delete_skipped=True,
                     integrity=False).exists()


# ---------------------------------------------------------------------------
# The wrapper
# ---------------------------------------------------------------------------

MODES_STUB = [(str(i), f"mode {i}", "") for i in range(9)]


def _wf(origin, dest, conv, **over):
    wf = {
        "mode": 3, "origin_format": origin, "dest_format": dest,
        "input_dir": ".", "workers": 2, "effort": 7, "distance": 0.1,
        "quality": 95, "staging": "", "compression": "zip", "bit_depth": 16,
        "conversion_type": conv,
        "advanced_options": {"delete_source": True, "delete_skipped": True},
    }
    wf.update(over)
    return wf


@pytest.mark.parametrize("origin,dest,conv", [
    ("jxl", "tiff", "jxl_tiff_decoder"),
    ("jpeg", "jxl", "transcode_lossless"),
    ("jxl", "jpeg", "jxl_to_jpeg_auto"),
])
def test_wrapper_emits_delete_skipped_for_every_direction(menu, launched, monkeypatch,
                                                          origin, dest, conv):
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True)
    # The lossy gate is exercised on its own below; here it must not block.
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, workflow: True)
    menu.execute_workflow(_wf(origin, dest, conv), STATUS)
    assert "--delete-skipped" in launched[-1]


# --- the lossy gate reaches repeats and presets, not just [D] --------------

def test_lossy_gate_fires_on_a_repeat(menu, monkeypatch, capsys):
    """A preset/repeat rebuilds advanced_options from the stored session and
    never walks through [D], so the gate has to live at execution time too."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    wf = _wf("jxl", "jpeg", "jxl_to_jpeg_force")
    assert menu._confirm_lossy_delete_skipped(wf) is True
    assert wf["advanced_options"]["delete_skipped"] is False, (
        "declining must turn the option off, not cancel the run")
    assert "nothing ties it to the original" in _out(capsys)


def test_lossy_gate_accepts(menu, monkeypatch):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    wf = _wf("jxl", "jpeg", "jxl_to_jpeg_force")
    menu._confirm_lossy_delete_skipped(wf)
    assert wf["advanced_options"]["delete_skipped"] is True


@pytest.mark.parametrize("conv,adv,dry", [
    # provable direction — no gate
    ("transcode_lossless", {"delete_source": True, "delete_skipped": True}, False),
    # encoder — no gate (it has --verify-roundtrip instead)
    ("jxl_tiff_encoder", {"delete_source": True, "delete_skipped": True}, False),
    # delete_skipped off — nothing to gate
    ("jxl_to_jpeg_force", {"delete_source": True}, False),
    # a dry run deletes nothing
    ("jxl_to_jpeg_force", {"delete_source": True, "delete_skipped": True}, True),
])
def test_lossy_gate_stays_quiet_when_it_should(menu, monkeypatch, conv, adv, dry):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    def _boom(*a):
        raise AssertionError("asked when it should not have")
    monkeypatch.setattr("builtins.input", _boom)
    wf = _wf("jxl", "jpeg", conv, advanced_options=adv, dry_run=dry)
    assert menu._confirm_lossy_delete_skipped(wf) is True


def test_lossy_gate_is_not_asked_twice(menu, monkeypatch):
    """[D] marks the workflow, so execution time does not re-ask."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    def _boom(*a):
        raise AssertionError("asked twice")
    monkeypatch.setattr("builtins.input", _boom)
    wf = _wf("jxl", "jpeg", "jxl_to_jpeg_force", _lossy_skip_confirmed=True)
    assert menu._confirm_lossy_delete_skipped(wf) is True


def test_wrapper_omits_it_without_delete_source(menu, launched, monkeypatch):
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True)
    menu.execute_workflow(_wf("jxl", "tiff", "jxl_tiff_decoder",
                              advanced_options={"delete_skipped": True}), STATUS)
    assert "--delete-skipped" not in launched[-1]


def _gateway(menu, monkeypatch, answers, origin, dest, conv):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))
    wf = {"origin_format": origin, "dest_format": dest, "input_dir": ".",
          "mode": None, "conversion_type": conv}
    ok = menu._wizard_delete_gateway(wf, MODES_STUB)
    return ok, wf


def test_D_lossy_direction_asks_an_extra_confirmation(menu, monkeypatch, capsys):
    """The one combination with no provenance of any kind gets its own gate,
    and declining it turns the option off rather than the whole run."""
    # gate1, layout, gate2, delete-skipped yes, EXTRA confirmation NO
    _ok, wf = _gateway(menu, monkeypatch, ["y", "3", "y", "y", "n"],
                       "jxl", "jpeg", "jxl_to_jpeg_force")
    assert wf["delete_source"] is True
    assert wf["delete_skipped"] is False
    assert "nothing ties it to the original" in _out(capsys)


def test_D_lossy_direction_can_still_be_confirmed(menu, monkeypatch):
    _ok, wf = _gateway(menu, monkeypatch, ["y", "3", "y", "y", "y"],
                       "jxl", "jpeg", "jxl_to_jpeg_force")
    assert wf["delete_skipped"] is True


def test_D_lossless_direction_has_no_extra_gate(menu, monkeypatch, capsys):
    """JPEG<->JXL can PROVE provenance, so it must not be nagged like the lossy
    path — and the wording tells the user which one they are in."""
    _ok, wf = _gateway(menu, monkeypatch, ["y", "3", "y", "y"],
                       "jpeg", "jxl", "transcode_lossless")
    assert wf["delete_skipped"] is True
    out = _out(capsys)
    assert "PROVES" in out
    assert "nothing ties it to the original" not in out


def test_D_decoder_direction_says_it_is_structural_only(menu, monkeypatch, capsys):
    _ok, wf = _gateway(menu, monkeypatch, ["y", "3", "y", "y"],
                       "jxl", "tiff", "jxl_tiff_decoder")
    assert wf["delete_skipped"] is True
    assert "nothing compares the contents" in _out(capsys)


# ---------------------------------------------------------------------------
# [D] must be reachable from EVERY mode selector
#
# It was added to the Step-4 menu alone, and the `choice == "8"` arming was
# removed from all three at the same time — so Auto Mode -> "pick manually" and
# the "?" detail view silently lost the delete workflow entirely. Nothing in the
# suite noticed, because [D] was only ever tested in isolation.
# ---------------------------------------------------------------------------

MODE_SELECTORS = [
    "_wizard_select_mode",
    "_wizard_select_mode_manual",
    "_show_mode_details_and_select",
]


@pytest.mark.parametrize("selector", MODE_SELECTORS)
def test_every_mode_selector_routes_D_to_the_gateway(menu, monkeypatch, selector):
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "D")
    seen = {}

    def _fake_gateway(self, workflow, modes):
        seen["modes"] = modes
        return "GATEWAY"

    monkeypatch.setattr(wp.InteractiveMenu, "_wizard_delete_gateway", _fake_gateway)

    wf = {"origin_format": "tiff", "dest_format": "jxl", "input_dir": ".",
          "conversion_type": "jxl_tiff_encoder"}
    assert getattr(menu, selector)(wf) == "GATEWAY", f"{selector} ignored [D]"
    # The gateway needs a usable layout list to offer
    assert [m[0] for m in seen["modes"]] == [str(i) for i in range(9)]


@pytest.mark.parametrize("selector", MODE_SELECTORS)
def test_every_mode_selector_still_takes_a_plain_layout(menu, monkeypatch, selector):
    """No over-fix: picking a number must still just set the mode."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "5")
    monkeypatch.setattr(wp.InteractiveMenu, "_wizard_delete_gateway",
                        lambda self, workflow, modes: pytest.fail("gateway on a plain layout"))
    wf = {"origin_format": "tiff", "dest_format": "jxl", "input_dir": "."}
    assert getattr(menu, selector)(wf) is True
    assert wf["mode"] == 5
    assert wf.get("delete_source") is not True


@pytest.mark.parametrize("selector", MODE_SELECTORS)
def test_no_mode_selector_arms_delete_on_mode_8(menu, monkeypatch, selector):
    """Mode 8 is 'in-place recursive' and keeps your files, everywhere."""
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr("builtins.input", lambda *a: "8")
    wf = {"origin_format": "tiff", "dest_format": "jxl", "input_dir": "."}
    getattr(menu, selector)(wf)
    assert wf["mode"] == 8
    assert wf.get("delete_source") is not True
