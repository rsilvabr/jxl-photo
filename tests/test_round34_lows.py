#!/usr/bin/env python3
"""Round-34 low-severity encoder batch.

None of these destroys data on its own; each makes the encoder lie a little
or die ugly:

  1. The real-run summary undercounted skipped: mode 6/7 files outside the
     export marker were counted in the DRY-RUN summary but not the real one.
  2. The script-set TEMP2_DIR staging path was never validated — an invalid
     value crashed mid-run with a raw traceback.
  3. Zero-page TIFFs: `split` classified them as corrupt (exit code unchanged)
     while `skip`/`ignore` fell through to a per-file ERROR (exit 1).
  4. The adopt-scan refusal verified against the CURRENT --distance without
     naming that as the likely cause of a mass refusal.
  5. Single-file mode-4/5 runs warned "Output outside input tree" on every
     legitimate output — the anchor was the file itself.
  6. --thumbnail-suffix accepted path separators and '..', writing thumbnails
     outside the destination.
  7. Mode 6's decoder-output skip honored EXPORT_TIFF_SUBFOLDER — a mode-7
     setting — so a leftover value made mode 6 re-encode decoded TIFFs.
  8. Stale-split detection stored the source stem in original case while the
     compared names were normcased — no match on Windows.
  9. _measure_batch_ratio's sort key statted without a guard: one TIFF
     vanishing between scan and preflight silently killed the whole estimate.

Pre-fix proof: run this file with JXL_ENCODER_UNDER_TEST pointing at the HEAD
copy of the encoder:

    git show HEAD:jxl_tiff_encoder.py > <tmp>/jxl_tiff_encoder.py
    $env:JXL_ENCODER_UNDER_TEST = "<tmp>/jxl_tiff_encoder.py"
    python -m pytest tests/test_round34_lows.py
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

# The pre-fix proof runs this file against the HEAD copy of the encoder; the
# env var points both the in-process import and the subprocess calls at it.
_REPO = Path(__file__).resolve().parent.parent
_OVERRIDE = os.environ.get("JXL_ENCODER_UNDER_TEST")
sys.path.insert(0, str(Path(_OVERRIDE).resolve().parent) if _OVERRIDE else str(_REPO))
import jxl_tiff_encoder as enc

ENCODER = _OVERRIDE or str(_REPO / "jxl_tiff_encoder.py")


def _run(*args, cwd):
    return subprocess.run([sys.executable, ENCODER, *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600, cwd=str(cwd),
                          stdin=subprocess.DEVNULL)


def _tiff(path: Path, value=1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.full((32, 32, 3), value, np.uint16),
                     photometric="rgb")


def _zero_page_tiff(path: Path) -> Path:
    """A structurally openable TIFF header whose IFD chain is empty."""
    path.write_bytes(b"II*\x00\x00\x00\x00\x00")  # first IFD offset = 0
    return path


# ─────────────────────────────────────────────
# 1. Real-run and dry-run summaries count the same skipped files
# ─────────────────────────────────────────────

def test_mode6_skipped_files_reach_the_real_run_summary(tmp_path, monkeypatch):
    """Files the mode-6 resolver rejects (returns None) were counted in the
    dry-run summary (skipped_files + multipage_skipped) but not the real one —
    and the wrapper aggregates both. The finder filters the same way as the
    resolver, so the only way to reach that counter is a resolver that says
    None; the summary arithmetic is what is under test."""
    t_in = tmp_path / "2024_EXPORT" / "16B_TIFF" / "a.tif"
    t_out = tmp_path / "2024_EXPORT" / "sRGB" / "b.tif"
    _tiff(t_in)
    _tiff(t_out)
    planned_jxl = tmp_path / "2024_EXPORT" / "16B_JXL" / "a.jxl"

    monkeypatch.setattr(enc, "find_tiffs_mode6", lambda root: [t_in, t_out])
    monkeypatch.setattr(
        enc, "resolve_output",
        lambda t, mode, root: None if t == t_out else planned_jxl)
    monkeypatch.setattr(enc, "_preflight_space", lambda *a, **k: None)
    monkeypatch.setattr(
        enc, "process_group",
        lambda items, workers, mode:
            [((str(t), p), "ok", str(j), None) for t, j, p, _th, _sf, _sp in items])
    summaries = []
    monkeypatch.setattr(enc, "emit_summary_json",
                        lambda enabled, **kw: summaries.append(kw) if enabled else None)

    argv = ["jxl_tiff_encoder.py", str(tmp_path), "--mode", "6",
            "--distance", "0", "--summary-json"]
    monkeypatch.setattr(sys, "argv", argv + ["--dry-run"])
    enc.main()
    monkeypatch.setattr(sys, "argv", argv)
    enc.main()

    dry = next(s for s in summaries if s.get("dry_run", False))
    real = next(s for s in summaries if not s.get("dry_run", False))
    assert dry["skipped"] == 1
    assert real["skipped"] == 1, \
        "real-run summary dropped the mode-6 out-of-marker skip the dry run counted"


# ─────────────────────────────────────────────
# 2. A script-set TEMP2_DIR is validated up front
# ─────────────────────────────────────────────

def test_script_set_staging_dir_is_validated(tmp_path, monkeypatch, capsys):
    """TEMP2_DIR is edited in the file itself, bypassing --staging's _check_dir.
    A value that cannot be created must be a clean parser.error, not a mid-run
    traceback at staging_dir.mkdir."""
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file", encoding="utf-8")   # mkdir below it fails
    monkeypatch.setattr(enc, "TEMP2_DIR", str(blocker / "sub"))
    monkeypatch.setattr(sys, "argv", ["jxl_tiff_encoder.py", str(tmp_path), "--mode", "0"])

    with pytest.raises(SystemExit) as exit_info:
        enc.main()

    assert exit_info.value.code == 2
    assert "TEMP2_DIR" in capsys.readouterr().err


# ─────────────────────────────────────────────
# 3. Zero-page TIFFs are corrupt in every multipage mode
# ─────────────────────────────────────────────

@pytest.mark.parametrize("mp_mode", ["split", "split_all", "skip", "ignore"])
def test_zero_page_tiff_is_unreadable_in_every_multipage_mode(tmp_path, monkeypatch, mp_mode):
    """split/split_all already raised UnreadableTiff (corrupt, exit code
    unchanged); skip fell through to idx=0 and ignore's fast path swallowed the
    empty IFD chain, both dying later in convert_one as a per-file ERROR."""
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", mp_mode)
    z = _zero_page_tiff(tmp_path / "zero.tif")

    with pytest.raises(enc.UnreadableTiff):
        enc.convert_multipage(z, tmp_path / "out", 0)


def test_thumbnail_only_tiff_still_encodes_page_0_in_skip_mode(tmp_path, monkeypatch):
    """Guard: the zero-page classification must not swallow the documented
    skip-mode case of a TIFF whose only page is a thumbnail."""
    monkeypatch.setattr(enc, "MULTIPAGE_TIFF_MODE", "skip")
    p = tmp_path / "thumb_only.tif"
    tifffile.imwrite(str(p), np.full((16, 16, 3), 500, np.uint16),
                     photometric="rgb", subfiletype=1)
    items = enc.convert_multipage(p, tmp_path / "out", 0)
    assert len(items) == 1 and items[0][2] == 0


# ─────────────────────────────────────────────
# 4. The adopt-scan refusal names the distance mismatch
# ─────────────────────────────────────────────

def test_adopt_scan_refusal_hints_at_the_archive_distance(tmp_path):
    """The scan verifies against the CURRENT --distance: a d=0.1 archive under
    a d=0 run refuses everything, and the message must say why and what to do."""
    src, jxl_out = tmp_path / "src", tmp_path / "jxl"
    _tiff(src / "a.tif")
    r = _run(src, jxl_out, "--mode", "2", "--distance", "0.1", "--strip",
             cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr   # markerless d=0.1 archive

    r = _run(src, jxl_out, "--mode", "2", "--distance", "0",
             "--delete-source", "--delete-confirm-off", "--provenance", "adopt",
             cwd=tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out                   # refused: nothing converted
    assert (src / "a.tif").exists(), "a refused source must never be deleted"
    assert "different --distance" in out, out
    assert "--no-adopt-scan" in out, out


# ─────────────────────────────────────────────
# 5. Single-file mode-4/5 runs don't warn about leaving the input tree
# ─────────────────────────────────────────────

@pytest.mark.parametrize("mode", [4, 5])
def test_single_file_mode45_does_not_warn(tmp_path, caplog, mode):
    """The anchor was the file itself, which no path can be 'under': every
    legitimate single-file mode-4/5 output was flagged as outside the tree."""
    f = tmp_path / "Shoot_TIFF" / "photo.tif"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    with caplog.at_level(logging.WARNING, logger="jxl_convert"):
        enc.resolve_output(f, mode, f)
    assert "Output outside input tree" not in caplog.text


def test_directory_input_root_file_still_warns(tmp_path, caplog):
    """Guard: for a DIRECTORY input, a root-level file genuinely lands outside
    the selected tree — that warning is the reason _warn_if_outside exists."""
    f = tmp_path / "photo.tif"
    f.write_bytes(b"x")
    with caplog.at_level(logging.WARNING, logger="jxl_convert"):
        enc.resolve_output(f, 5, tmp_path)
    assert "Output outside input tree" in caplog.text


# ─────────────────────────────────────────────
# 6. --thumbnail-suffix must be a plain filename suffix
# ─────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["..", "../up", "a/b", "a\\b"])
def test_thumbnail_suffix_with_a_path_separator_is_refused(tmp_path, bad):
    """The suffix is glued into the output filename; separators or '..' write
    thumbnails outside the destination folder."""
    r = _run(tmp_path, "--mode", "0", "--thumbnail-suffix", bad, cwd=tmp_path)
    assert r.returncode == 2
    assert "--thumbnail-suffix" in r.stderr


def test_a_plain_thumbnail_suffix_is_still_accepted(tmp_path):
    r = _run(tmp_path, "--mode", "0", "--thumbnail-suffix", "_preview",
             cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


# ─────────────────────────────────────────────
# 7. Mode 6 skips decoder outputs regardless of EXPORT_TIFF_SUBFOLDER
# ─────────────────────────────────────────────

def test_mode6_skips_decoder_outputs_despite_a_leftover_subfolder(tmp_path, monkeypatch):
    """EXPORT_TIFF_SUBFOLDER is a mode-7 setting. A leftover value equal to the
    decoder's folder name used to exempt it from mode 6's skip — re-encoding
    decoded TIFFs (generational loss at d>0)."""
    monkeypatch.setattr(enc, "EXPORT_TIFF_SUBFOLDER", "16b_tiff")
    decoded = tmp_path / "2024_EXPORT" / "16b_tiff" / "photo.tif"
    decoded.parent.mkdir(parents=True)
    decoded.write_bytes(b"")

    assert enc.find_tiffs_mode6(tmp_path) == [], \
        "mode 6 must skip decoder output folders no matter what subfolder is set"


def test_mode7_still_honors_the_requested_subfolder(tmp_path, monkeypatch):
    """Guard: the exemption is mode-7 semantics and must survive there — an
    explicit user request to scan the decoder's folder wins."""
    monkeypatch.setattr(enc, "EXPORT_TIFF_SUBFOLDER", "16b_tiff")
    decoded = tmp_path / "2024_EXPORT" / "16b_tiff" / "photo.tif"
    decoded.parent.mkdir(parents=True)
    decoded.write_bytes(b"")

    assert enc.find_tiffs_mode7(tmp_path) == [decoded]


# ─────────────────────────────────────────────
# 8. Stale-split detection normcases the source stems
# ─────────────────────────────────────────────

def test_stale_split_detection_matches_case_insensitively(tmp_path):
    """The destination listing is normcased but the source stems were not: a
    leftover scan_page2.jxl next to a re-split Scan.tif was never flagged."""
    dest = tmp_path / "out"
    dest.mkdir()
    leftover = dest / "scan_page2.jxl"
    leftover.write_bytes(b"x")
    tiff = tmp_path / "Scan.tif"          # only its stem is used
    final = dest / "Scan.jxl"             # this run's planned page-0 output
    tasks = [(tiff, final, final, 0, False, 0, 3, "group-1", 3)]

    stale = enc._warn_stale_split_outputs(tasks)

    assert leftover in stale


# ─────────────────────────────────────────────
# 9. A vanished file must not kill the batch estimate
# ─────────────────────────────────────────────

def test_measure_batch_ratio_survives_a_vanished_file(tmp_path):
    """The sort key statted each TIFF unguarded; a file deleted between scan
    and preflight raised OSError, which main's catch-all logged at DEBUG — the
    whole estimate silently vanished."""
    gone = tmp_path / "gone.tif"          # never created: vanishes by existing
    ratio, samples = enc._measure_batch_ratio([gone], 0.05, 7)
    assert ratio is None and samples == []
