#!/usr/bin/env python3
"""Regressions for the round-29 audit fixes (bugs #277-#281).

Every one was reproduced against the shipped code before the fix:

  * #277 — exit 2 means both "argparse rejected this command line" and "the run
    stopped to protect your files", and the wrapper reported both as the
    second. A wrapper that emitted a flag the child does not have looked, from
    the outside, exactly like a duplicate-output abort, and sent the user
    hunting for a collision that never existed. The manifest recap made it
    worse by adding "Nothing was deleted" — which is also not something exit 2
    can promise, since the disk-full abort fires part way through a run.
  * #278 — --provenance adopt exists only in the encoder, but the wizard
    offered it in every collapsing mode and all six emission sites passed the
    answer through, so a JXL->TIFF or JPEG<->JXL delete run built a command
    line that died at argparse. The decoder's own --none warning told the user
    to pass a flag the decoder does not have.
  * #279 — the encoder's provenance block ran BEFORE the dry-run gate, so
    `--dry-run --provenance adopt` decoded every unmarked output and then wrote
    jxlphoto-src/srcsum into real JXLs with exiftool -overwrite_original. Same
    class as #235. It also ran before the delete confirmation, so declining
    (exit 3) left the archive stamped anyway.
  * #280 — mode 0 honours the output folder and then writes every file into it
    flat, exactly like mode 2, but _COLLAPSING_MODES never included it. Mode 0
    is also flat, so _abort_on_duplicate_outputs cannot see the clash either.
    Reproduced: A decoded to out/foto.tif with its JXL deleted, then B
    overwrote it and had its JXL deleted too — A's photo gone.
  * #281 — cmd_auto subtracted provenance refusals from the progress total and
    nothing else, so an auto run that refused every file exited 0 with an empty
    failure list. cmd_transcode and cmd_convert both count them.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp
import jxl_jpeg_transcoder as tr
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
ENCODER = str(REPO / "jxl_tiff_encoder.py")


def _menu():
    cfg = wp.ConfigManager()
    return wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))


# ── #277: argparse exit 2 is not a safety abort ─────────────────────────────

def test_usage_error_captured_from_child_output():
    """argparse writes '<prog>: error: ...' to stderr and exits 2."""
    menu = _menu()
    rc = menu._stream_child(
        [sys.executable, str(REPO / "jxl_tiff_decoder.py"),
         "nonexistent_input", "--provenance", "adopt"])
    assert rc == 2
    assert menu._last_child_usage_error is not None
    assert "invalid choice" in menu._last_child_usage_error


def test_safety_abort_is_not_reported_as_usage_error():
    """A child that exits 2 without argparse's error line stays a safety abort."""
    menu = _menu()
    rc = menu._stream_child(
        [sys.executable, "-c",
         "print('Aborting: duplicate output destinations'); raise SystemExit(2)"])
    assert rc == 2
    assert menu._last_child_usage_error is None


def test_usage_error_does_not_survive_into_the_next_run():
    """A stale usage error would mislabel the NEXT run's safety abort."""
    menu = _menu()
    menu._stream_child(
        [sys.executable, str(REPO / "jxl_tiff_decoder.py"),
         "nonexistent_input", "--provenance", "adopt"])
    assert menu._last_child_usage_error is not None
    menu._stream_child([sys.executable, "-c", "raise SystemExit(2)"])
    assert menu._last_child_usage_error is None


def test_usage_error_regex_ignores_ordinary_error_lines():
    """The children log plenty of lines containing 'error'; only argparse's own
    '<prog>.py: error: ...' shape counts."""
    for line in ("ERROR: required tool(s) not found in PATH: cjxl",
                 "  -> C:/photos/a.tif (error: could not read)",
                 "2026-01-01 12:00:00 | ERROR | conversion error: bad file"):
        assert wp._CHILD_USAGE_ERROR_RE.match(line) is None
    m = wp._CHILD_USAGE_ERROR_RE.match(
        "jxl_tiff_decoder.py: error: argument --provenance: invalid choice: 'adopt'")
    assert m is not None
    assert m.group(2).startswith("argument --provenance")


def test_single_run_exit2_message_names_the_usage_error(monkeypatch, capsys):
    """The message a user actually reads must say the command was rejected and
    that nothing was touched — not that a safety check fired."""
    menu = _menu()
    printed = []
    monkeypatch.setattr(menu, "_print_error", lambda m: printed.append(m))

    def _fake_stream(cmd, idle_timeout=3600):
        menu._last_child_usage_error = "argument --provenance: invalid choice: 'adopt'"
        return 2

    monkeypatch.setattr(menu, "_stream_child", _fake_stream)
    workflow = {
        "origin_format": "jxl", "dest_format": "tiff", "mode": 0,
        "input_dir": str(REPO), "output_dir": str(REPO),
        "workers": 1, "mode_config": {}, "advanced_options": {},
        "compression": "lzw", "bit_depth": 16,
    }
    menu.execute_workflow(workflow, {})
    blob = "\n".join(printed)
    assert "invalid choice" in blob
    assert "Nothing ran" in blob
    assert "safety check" not in blob


def test_manifest_exit2_does_not_promise_nothing_was_deleted():
    """Exit 2 is also the disk-full abort, which fires part way through a run,
    and completed entries did their own deleting."""
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    assert "Nothing was deleted; fix the cause" not in src, (
        "the manifest abort message still promises nothing was deleted")
    assert "stay deleted" in src


# ── #278: adopt is an encoder-only flag ─────────────────────────────────────

CHILD_ADOPT_SUPPORT = [
    ("jxl_tiff_encoder.py", True),
    ("jxl_tiff_decoder.py", False),
    ("jxl_jpeg_transcoder.py", False),
]


@pytest.mark.parametrize("script,supported", CHILD_ADOPT_SUPPORT)
def test_which_children_actually_accept_adopt(script, supported):
    """The predicate the wrapper trusts must match the argparse reality."""
    r = subprocess.run(
        [sys.executable, str(REPO / script), "in", "--provenance", "adopt"],
        capture_output=True, text=True)
    rejected = "invalid choice: 'adopt'" in (r.stderr + r.stdout)
    assert rejected is not supported, f"{script}: adopt support changed"


def test_supports_provenance_adopt_matches_the_children():
    assert wp._supports_provenance_adopt("tiff", "jxl") is True
    for origin, dest in (("jxl", "tiff"), ("jpeg", "jxl"), ("jxl", "jpeg"),
                         ("jxl", "png"), ("png", "jxl")):
        assert wp._supports_provenance_adopt(origin, dest) is False


@pytest.mark.parametrize("origin,dest", [("jxl", "tiff"), ("jpeg", "jxl"),
                                         ("jxl", "jpeg"), ("jxl", "png")])
def test_stored_adopt_is_downgraded_not_emitted(origin, dest, capsys):
    """A saved session or preset carrying adopt from a TIFF->JXL run must not
    be replayed at a script that has no such choice."""
    menu = _menu()
    cmd = []
    menu._append_provenance_flags(cmd, {"provenance": "adopt", "adopt_scan": False},
                                  origin, dest)
    assert "adopt" not in cmd
    assert "--no-adopt-scan" not in cmd
    assert cmd == ["--provenance", "path"], cmd


def test_adopt_still_emitted_for_the_encoder():
    menu = _menu()
    cmd = []
    menu._append_provenance_flags(cmd, {"provenance": "adopt", "adopt_scan": False},
                                  "tiff", "jxl")
    assert cmd == ["--provenance", "adopt", "--no-adopt-scan"]


def test_no_adopt_scan_is_never_emitted_without_adopt():
    """It is an encoder-only flag AND inert without adopt; the decoder's two
    emission sites used to append it for path/content runs as well."""
    menu = _menu()
    for pv in ("path", "content"):
        cmd = []
        menu._append_provenance_flags(cmd, {"provenance": pv, "adopt_scan": False},
                                      "tiff", "jxl")
        assert cmd == ["--provenance", pv]


def test_no_emission_site_passes_provenance_by_hand():
    """Six sites had already drifted; they must all go through the one helper."""
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    assert "cmd.extend(['--provenance'" not in src.replace(
        "cmd.extend(['--provenance', pv])", "")


def test_wizard_builds_its_choices_from_the_predicate():
    """Gating the OFFER, not just the emission: a menu entry the target script
    cannot accept is worse than no menu entry at all. The wizard used to hand
    Prompt.ask a hardcoded three-item list for every direction."""
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    assert '["path", "content", "adopt"]' not in src
    assert '(path/content/adopt)' not in src
    assert "_pv_choices = [\"path\", \"content\"] + ([\"adopt\"] if _adopt_ok else [])" in src
    assert "_adopt_ok = _supports_provenance_adopt(origin, dest)" in src


def test_decoder_none_warning_does_not_name_a_flag_it_lacks():
    """The remedy the message named was impossible to follow."""
    src = (REPO / "jxl_tiff_decoder.py").read_text(encoding="utf-8")
    assert "pass --provenance adopt" not in src
    assert "--provenance adopt has no counterpart here" in src


def test_bug_tracker_no_longer_claims_adopt_in_three_scripts():
    doc = (REPO / "docs" / "bug_tracking_since_v1.0.md").read_text(encoding="utf-8")
    row = next(l for l in doc.splitlines() if l.startswith("| 271 |"))
    assert "encoder, decoder, transcoder" not in row


@pytest.mark.parametrize("readme", [
    "README_jxl_tiff_encoder.md", "README_jxl_tiff_decoder.md",
    "README_jxl_jpeg_transcoder.md",
])
def test_provenance_is_documented_in_every_backend_readme(readme):
    doc = (REPO / "docs" / readme).read_text(encoding="utf-8")
    assert "--provenance" in doc
    if readme == "README_jxl_tiff_encoder.md":
        assert "--no-adopt-scan" in doc
        assert "path|content|adopt" in doc
    else:
        # Must say plainly that it has no adopt, since the wrapper's menu and
        # the encoder's docs both mention one.
        assert "NO `adopt`" in doc


# ── #279: the provenance block must respect dry-run and the delete gate ─────

def _tiff(path: Path, value: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((48, 48, 3), value, np.uint16),
                     photometric="rgb")


def _enc(*args, cwd):
    return subprocess.run([sys.executable, ENCODER, *args], capture_output=True,
                          text=True, timeout=600, cwd=str(cwd),
                          stdin=subprocess.DEVNULL)


def _legacy_archive(tmp_path):
    """An archive that predates the markers, with its source still in place."""
    _tiff(tmp_path / "root" / "A" / "foto.tif", 1000)
    r = _enc("root", "--mode", "5", "--distance", "0", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    for j in (tmp_path / "root" / "JXL_16bits").glob("*.jxl"):
        subprocess.run(["exiftool", "-overwrite_original", "-XMP-dc:Relation=", str(j)],
                       capture_output=True, timeout=60)
    return tmp_path / "root" / "A" / "foto.tif", tmp_path / "root" / "JXL_16bits" / "foto.jxl"


def _is_unmarked(jxl: Path) -> bool:
    m = enc._read_source_markers_batch([jxl])[str(jxl)]
    return not (m["src"] or m["srcsum"])


ARCHIVE = ["--mode", "5", "--distance", "0", "--delete-source",
           "--delete-skipped", "--provenance", "adopt"]


def test_dry_run_with_adopt_does_not_stamp_the_archive(tmp_path):
    """A simulation that MODIFIES the files it is simulating over is the one
    thing --dry-run promises never to do."""
    src, jxl = _legacy_archive(tmp_path)
    assert _is_unmarked(jxl)

    r = _enc("root", *ARCHIVE, "--delete-confirm-off", "--dry-run", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert _is_unmarked(jxl), "the dry run stamped a real JXL"
    assert src.exists(), "the dry run deleted a source"


def test_dry_run_reports_what_adopt_would_do(tmp_path):
    """Skipping the work is only right if the simulation still says what would
    happen — and says it is an upper bound, since the scan can refuse."""
    _legacy_archive(tmp_path)
    r = _enc("root", *ARCHIVE, "--delete-confirm-off", "--dry-run", cwd=tmp_path)
    out = r.stdout
    assert "carry no provenance record" in out
    assert "upper bound" in out
    # The real work must NOT have been reported as done.
    assert "verified by the adopt scan" not in out


def test_declining_the_delete_confirmation_leaves_nothing_stamped(tmp_path):
    """Exit 3 means the run was called off. It used to have already rewritten
    the user's archive by then."""
    src, jxl = _legacy_archive(tmp_path)
    # stdin is /dev/null: the confirmation reads EOF and fails closed.
    r = _enc("root", *ARCHIVE, cwd=tmp_path)
    assert r.returncode == 3, r.stdout
    assert _is_unmarked(jxl), "a declined run stamped the archive anyway"
    assert src.exists()


def test_a_confirmed_run_still_stamps(tmp_path):
    """The healing pass must survive the reordering."""
    src, jxl = _legacy_archive(tmp_path)
    r = _enc("root", *ARCHIVE, "--delete-confirm-off", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    assert not _is_unmarked(jxl), "adoption no longer stamps"
    assert not src.exists(), "the adopted source was not deleted"


def test_stamping_happens_after_the_confirmation_in_source_order():
    """Cheap guard on the ordering the tests above prove behaviourally: the
    exiftool write must not drift back above the gate."""
    src = (REPO / "jxl_tiff_encoder.py").read_text(encoding="utf-8")
    confirm = src.index("if not confirm_deletion_tiff(is_lossy):")
    stamp = src.index("if provenance_to_stamp:")
    dry_gate = src.index("    # Dry run\n    if args.dry_run:")
    assert dry_gate < confirm < stamp, "stamping drifted back before a gate"


# ── #280: mode 0 with an output folder collapses just like mode 2 ───────────

DECODER = str(REPO / "jxl_tiff_decoder.py")


def _dec(*args, cwd):
    return subprocess.run([sys.executable, DECODER, *args], capture_output=True,
                          text=True, timeout=600, cwd=str(cwd),
                          stdin=subprocess.DEVNULL)


def _archive(tmp_path, folder: str, value: int):
    """One TIFF in `folder`, encoded to a JXL beside it."""
    _tiff(tmp_path / folder / "foto.tif", value)
    r = _enc(folder, "--mode", "0", "--distance", "0", cwd=tmp_path)
    assert r.returncode == 0, r.stdout
    return tmp_path / folder / "foto.jxl"


def test_mode0_with_output_folder_is_collapsing():
    """resolve_output mode 0 sends every file to the given folder, flat —
    exactly what mode 2 does."""
    import jxl_tiff_decoder as dec
    assert dec._run_collapses_structure(0, "D:/out", "D:/in") is True
    assert dec._run_collapses_structure(0, None, "D:/in") is False
    # Same folder spelled differently is still in place, not a collapse.
    assert dec._run_collapses_structure(0, "D:/in", "D:/in") is False
    assert dec._run_collapses_structure(0, r"d:\IN" + "\\", "D:/in") is False
    # The always-collapsing modes are unchanged, with or without an output.
    for m in (2, 4, 5, 6, 7):
        assert dec._run_collapses_structure(m, None, "D:/in") is True
    # ...and the genuinely per-source modes stay out of it.
    for m in (1, 3, 8):
        assert dec._run_collapses_structure(m, "D:/out", "D:/in") is False


def test_mode0_in_place_is_not_guarded(tmp_path):
    """Fail-closed must not become 'refuse everything': demanding a marker for
    an in-place mode 0 would be #271's dead end all over again."""
    jxl = _archive(tmp_path, "A", 1000)
    subprocess.run(["exiftool", "-overwrite_original", "-XMP-dc:Relation=", str(jxl)],
                   capture_output=True, timeout=60)
    (tmp_path / "A" / "foto.tif").unlink()
    # Decode it back in place, twice: the second run finds its own output and
    # must not refuse it for lacking a marker.
    assert _dec("A", "--mode", "0", cwd=tmp_path).returncode == 0
    r = _dec("A", "--mode", "0", "--delete-source", "--delete-confirm-off",
             "--delete-skipped", cwd=tmp_path)
    assert "REFUSING" not in r.stdout, r.stdout


def test_mode0_to_a_shared_output_folder_refuses_the_second_source(tmp_path):
    """The data-loss case: A is decoded to out/foto.tif and its JXL deleted, so
    out/foto.tif is the ONLY copy of A. B must not be allowed to overwrite it
    and delete its own JXL too — that loses A's photo for good."""
    jxl_a = _archive(tmp_path, "A", 1000)
    jxl_b = _archive(tmp_path, "B", 60000)
    (tmp_path / "A" / "foto.tif").unlink()
    (tmp_path / "B" / "foto.tif").unlink()
    out = tmp_path / "out"

    r1 = _dec("A", "out", "--mode", "0", "--delete-source", "--delete-confirm-off",
              cwd=tmp_path)
    assert r1.returncode == 0, r1.stdout
    assert not jxl_a.exists(), "run 1 kept its source; the test proves nothing"
    assert (out / "foto.tif").exists()

    r2 = _dec("B", "out", "--mode", "0", "--delete-source", "--delete-confirm-off",
              "--overwrite", cwd=tmp_path)
    assert "REFUSING" in r2.stdout, r2.stdout
    assert jxl_b.exists(), "B's JXL was deleted anyway"
    # A's only remaining copy still holds A's pixels.
    with tifffile.TiffFile(str(out / "foto.tif")) as tif:
        assert int(tif.pages[0].asarray().flat[0]) == 1000, "A's photo was overwritten"


# ── #281: cmd_auto swallowed provenance refusals ────────────────────────────

TRANSCODER = str(REPO / "jxl_jpeg_transcoder.py")


def _tr(*args, cwd):
    return subprocess.run([sys.executable, TRANSCODER, *args], capture_output=True,
                          text=True, timeout=600, cwd=str(cwd),
                          stdin=subprocess.DEVNULL)


def _jpeg(path: Path, seed: int):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:96, 0:128]
    a = ((np.sin((x + seed) / 9.0) * .25 + np.cos((y + seed) / 11.0) * .25 + .5) * 255)
    Image.fromarray(np.stack([a.astype(np.uint8)] * 3, axis=2)).save(str(path), quality=92)


AUTO_ARCHIVE = ["--mode", "5", "--delete-source", "--delete-confirm-off"]


def _refusing_auto_run(tmp_path):
    """A collapsing auto run whose only file is refused: B's output already
    exists and was made by A.

    Each run is pointed at ONE source folder, because mode 5 writes to a
    sibling of it — scanning the shared parent would make auto mode pick its
    own JXL output back up and decode it, which is a different story.
    """
    _jpeg(tmp_path / "root" / "A" / "foto.jpg", 0)
    r1 = _tr("root/A", *AUTO_ARCHIVE, cwd=tmp_path)
    assert r1.returncode == 0, r1.stdout
    assert not (tmp_path / "root" / "A" / "foto.jpg").exists(), r1.stdout

    _jpeg(tmp_path / "root" / "B" / "foto.jpg", 50)
    return _tr("root/B", *AUTO_ARCHIVE, "--delete-skipped", "--summary-json",
               cwd=tmp_path)


def test_auto_run_that_refuses_everything_does_not_exit_zero(tmp_path):
    """A scheduled auto job saw a clean run: exit 0, no failures listed."""
    r = _refusing_auto_run(tmp_path)
    assert "REFUSING" in r.stdout, r.stdout
    assert r.returncode != 0, "an auto run that refused every file exited 0"
    assert (tmp_path / "root" / "B" / "foto.jpg").exists(), "B was deleted"


def test_auto_run_reports_refusals_in_the_summary_failures(tmp_path):
    """The wrapper's manifest recap reads this list; it was empty."""
    import json
    r = _refusing_auto_run(tmp_path)
    line = next(l for l in r.stdout.splitlines()
                if l.lstrip().startswith(tr.SUMMARY_PREFIX))
    summary = json.loads(line.lstrip()[len(tr.SUMMARY_PREFIX):])
    assert summary["errors"] >= 1, summary
    assert summary["failures"], "refusals never reached the failure list"
    assert "refused" in summary["failures"][0]["reason"]
