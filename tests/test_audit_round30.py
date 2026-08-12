#!/usr/bin/env python3
"""Bugs #297-#302 — round-30 audit, found by running the toolkit against real
16-bit Capture One exports and 756 MB RGB+IR film scans.

The conversion path itself came through clean: every lossless round trip was
pixel-identical, including the 3-page scans (RGB + thumbnail + IR MASK page)
with their 217 KB scanner ICC. All six findings sit around it.

  * #297 — _count_origin_files applied the export MARKER to the child module but
    never the SUBFOLDER, so mode 7 counted every subfolder under the marker for
    a run that converts exactly one of them. Not merely a larger number: a
    DIFFERENT SET OF FILES, printed in the "About to delete originals" panel
    whose visible count is what catches a wrong folder before the HHMM token.
    _manifest_output_collisions already applied both — the two sites disagreed.
  * #298 — _manifest_output_collisions applied marker and subfolder to the
    imported child modules and restored NEITHER. The wrapper is a long-lived
    interactive process, so those values leaked into every later in-process use
    of that child for the rest of the menu session, which made #297's count
    depend on what had been run before it.
  * #299 — the encoder's opening banner printed the raw THUMBNAIL_MODE even
    under --multipage-mode split_all, which ignores that setting and encodes
    every page. The line said "Thumbnail: exclude" immediately above its own
    log of a written *_thumbnail.jxl, and the README documents that banner as
    showing the settings that are ACTIVE.
  * #300 — README_jxl_tiff_decoder.md still documented SubfileType=4 (MASK) as
    being rewritten to PAGE (2). _page_subfiletype_kwargs has written the raw
    TIFF tag since that was fixed, so a scanner's IR page keeps its role; the
    doc described a bug that no longer exists, in the film-scan section.
  * #301 — _verify_tiff_integrity's comment claimed "Only the last strip/tile is
    decoded" while asarray() decodes the whole page (~187 MB for a 93 MP scan).
    Comment-only: the cost is accepted deliberately, but a maintainer reading it
    would have believed the gate was cheap.
  * #302 — the wrapper reconfigured stdout with errors="replace" but no
    encoding, so a REDIRECTED stdout fell back to the ANSI codepage and turned
    both the available (✓) and missing (✗) icons into the same "?". The three
    backend scripts already passed encoding="utf-8"; the wrapper's own comment
    claimed it did too.
"""

import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_jpeg_transcoder as tr
import jxl_photo as wp
import jxl_tiff_decoder as dec
import jxl_tiff_encoder as enc

REPO = Path(__file__).resolve().parent.parent
ENCODER = str(REPO / "jxl_tiff_encoder.py")


def _menu():
    """An InteractiveMenu with just enough state for the helpers under test."""
    menu = wp.InteractiveMenu.__new__(wp.InteractiveMenu)
    menu.config = wp.ConfigManager()
    return menu


def _marker_tree(root: Path):
    """_EXPORT holding three sibling subfolders, one file of each type in each.

    16B_TIFF is also a decoder-output folder name, so with no subfolder filter
    the encoder's finder skips it and keeps the other two — which is what makes
    the wrong count point at the wrong FILES, not just at more of them.
    """
    for sub in ("16B_TIFF", "AdobeRGB", "sRGB"):
        d = root / "shoot" / "_EXPORT" / sub
        d.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(str(d / "img.tif"),
                         np.zeros((16, 16, 3), np.uint16), photometric="rgb")
        (d / "img.jxl").write_bytes(b"\x00\x00\x00\x0cJXL \r\n\x87\n")
        (d / "img.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)


# ── #297 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "origin,dest,child,finder,global_name",
    [
        ("tiff", "jxl", enc, "find_tiffs_mode7", "EXPORT_TIFF_SUBFOLDER"),
        ("jxl", "tiff", dec, "find_jxls_mode7", "EXPORT_JXL_SUBFOLDER"),
    ],
)
def test_delete_count_honors_export_subfolder(tmp_path, origin, dest, child,
                                              finder, global_name):
    """The delete panel's count must equal what the child actually processes."""
    _marker_tree(tmp_path)
    workflow = {
        "origin_format": origin, "dest_format": dest,
        "input_dir": str(tmp_path),
        "mode_config": {"export_marker": "_EXPORT",
                        "export_subfolder": "16B_TIFF"},
    }
    counted = _menu()._count_origin_files(workflow, 7)

    was_disabled = child.logger.disabled
    saved = getattr(child, global_name)
    child.logger.disabled = True
    try:
        setattr(child, global_name, "16B_TIFF")
        real = len(getattr(child, finder)(tmp_path))
    finally:
        setattr(child, global_name, saved)
        child.logger.disabled = was_disabled

    assert real == 1, "fixture should leave exactly one file inside the subfolder"
    assert counted == real, (
        f"the delete confirmation announced {counted} file(s) for a run that "
        f"touches {real} — and they are different files"
    )


def test_delete_count_honors_export_subfolder_transcoder(tmp_path):
    """Same for the transcoder, which filters inside its resolver, not a finder."""
    _marker_tree(tmp_path)
    workflow = {
        "origin_format": "jpeg", "dest_format": "jxl",
        "input_dir": str(tmp_path),
        "mode_config": {"export_marker": "_EXPORT",
                        "export_subfolder": "16B_TIFF"},
    }
    counted = _menu()._count_origin_files(workflow, 7)
    assert counted == 1, (
        f"the transcoder branch ignored --export-subfolder and counted {counted}"
    )


def test_delete_count_still_counts_everything_without_a_subfolder(tmp_path):
    """An empty subfolder means "all of them" — the fix must not over-filter."""
    _marker_tree(tmp_path)
    workflow = {
        "origin_format": "tiff", "dest_format": "jxl",
        "input_dir": str(tmp_path),
        "mode_config": {"export_marker": "_EXPORT", "export_subfolder": ""},
    }
    # 16B_TIFF is a decoder-output folder, so the encoder's finder drops it and
    # the other two remain.
    assert _menu()._count_origin_files(workflow, 7) == 2


# ── #298 ──────────────────────────────────────────────────────────────────────

def test_collision_scan_restores_child_globals(tmp_path):
    """The scan must put marker and subfolder back on every child module."""
    _marker_tree(tmp_path)
    watched = ("EXPORT_MARKER", "EXPORT_TIFF_SUBFOLDER", "EXPORT_JXL_SUBFOLDER",
               "EXPORT_JPEG_SUBFOLDER")
    before = {(m.__name__, g): getattr(m, g)
              for m in (enc, dec, tr) for g in watched if hasattr(m, g)}

    _menu()._manifest_output_collisions(
        [(str(tmp_path / "shoot"), "", 7)], {".tif"},
        origin="tiff", dest="jxl",
        export_marker="ZZ_MARKER", export_subfolder="ZZ_SUBFOLDER",
    )

    after = {(m.__name__, g): getattr(m, g)
             for m in (enc, dec, tr) for g in watched if hasattr(m, g)}
    leaked = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not leaked, f"the collision scan leaked child globals: {leaked}"


def test_child_marker_context_restores_after_an_exception():
    """A resolver that raises must not leave the module rewritten either."""
    saved = (enc.EXPORT_MARKER, enc.EXPORT_TIFF_SUBFOLDER)
    with pytest.raises(RuntimeError):
        with wp._with_child_marker(enc, "TMP_MARK", "TMP_SUB"):
            assert enc.EXPORT_MARKER == "TMP_MARK"
            assert enc.EXPORT_TIFF_SUBFOLDER == "TMP_SUB"
            raise RuntimeError("boom")
    assert (enc.EXPORT_MARKER, enc.EXPORT_TIFF_SUBFOLDER) == saved


# ── #299 ──────────────────────────────────────────────────────────────────────

def _banner(tmp_path, *extra):
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(src / "m.tif"),
                     np.zeros((3, 8, 8, 3), np.uint16), photometric="rgb")
    r = subprocess.run(
        [sys.executable, ENCODER, str(src), str(tmp_path / "out"),
         "--mode", "2", "--distance", "0", "--dry-run", *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, stdin=subprocess.DEVNULL)
    line = next((l for l in (r.stdout + r.stderr).splitlines()
                 if "Multi-page:" in l), "")
    assert line, f"no banner line in output:\n{r.stdout}\n{r.stderr}"
    return line


def test_banner_reports_the_thumbnail_policy_split_all_actually_uses(tmp_path):
    line = _banner(tmp_path, "--multipage-mode", "split_all",
                   "--thumbnail-mode", "exclude")
    thumb = re.search(r"Thumbnail: ([^|]+)", line).group(1).strip()
    assert not thumb.startswith("exclude"), (
        f"banner claims {thumb!r} while split_all encodes every thumbnail: {line}"
    )
    assert "include" in thumb


def test_banner_still_reports_the_real_policy_outside_split_all(tmp_path):
    line = _banner(tmp_path, "--multipage-mode", "split",
                   "--thumbnail-mode", "exclude")
    assert re.search(r"Thumbnail: exclude", line), line


# ── #300 / #301 ───────────────────────────────────────────────────────────────

def test_mask_subfiletype_is_written_as_a_raw_tag_not_demoted():
    """4 (MASK) must survive; tifffile's own parameter would reject it."""
    kwargs = dec._page_subfiletype_kwargs(4)
    assert "subfiletype" not in kwargs, "4 must not go through tifffile's enum"
    assert kwargs["extratags"] == [(254, 4, 1, 4, True)]
    # The values tifffile does accept keep using the documented parameter.
    assert dec._page_subfiletype_kwargs(1) == {"subfiletype": 1}
    assert dec._page_subfiletype_kwargs(2) == {"subfiletype": 2}
    assert dec._page_subfiletype_kwargs(0) == {}


def test_decoder_readme_does_not_still_promise_mask_is_demoted():
    """The film-scan section described a bug that was already fixed."""
    text = (REPO / "docs" / "README_jxl_tiff_decoder.md").read_text(encoding="utf-8")
    section = text.split("## Multi-Page TIFF Reconstruction")[-1]
    assert not re.search(
        r"`SubfileType=4` \(MASK\) is mapped to `PAGE`", section), (
        "README still documents the pre-fix MASK -> PAGE demotion")
    assert "including `SubfileType=4` (MASK)" in section


def test_integrity_comment_does_not_claim_a_single_strip_is_read():
    """asarray() decodes the whole page; the comment used to say otherwise."""
    src = (REPO / "jxl_tiff_decoder.py").read_text(encoding="utf-8")
    body = src.split("def _verify_tiff_integrity")[1].split("\ndef ")[0]
    assert "Only the last strip/tile is decoded" not in body
    assert "decodes the WHOLE last page" in body


# ── #302 ──────────────────────────────────────────────────────────────────────

def test_wrapper_reconfigures_stdout_with_an_encoding():
    """errors= alone collapses ✓ and ✗ onto the same '?' when redirected."""
    src = (REPO / "jxl_photo.py").read_text(encoding="utf-8")
    head = src[:src.index("from rich.console import Console")]
    calls = re.findall(r"sys\.std(?:out|err)\.reconfigure\(([^)]*)\)", head)
    assert calls, "the wrapper no longer reconfigures its streams"
    for args in calls:
        assert "encoding=" in args, (
            f"reconfigure({args}) has no encoding: redirected output falls back "
            f"to the ANSI codepage and every status icon becomes '?'")


def test_dependency_icons_survive_a_redirected_stdout():
    """End to end: the icons must still be distinguishable through a pipe."""
    r = subprocess.run([sys.executable, str(REPO / "jxl_photo.py"), "--list-presets"],
                       capture_output=True, timeout=300, cwd=str(REPO),
                       stdin=subprocess.DEVNULL)
    out = r.stdout.decode("utf-8", errors="replace")
    assert "[✓]" in out or "[✗]" in out, (
        f"no status icon survived the redirect; got:\n{out[:400]}")
