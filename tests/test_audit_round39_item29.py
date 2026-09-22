"""Item 29 (2026-09-21 audit) — the modes-4/5 "Output outside input tree"
warning must not fire for legitimate SINGLE-FILE runs.

The first fix attempt anchored on `input_root.is_file()`, but the callers
disagree on what input_root means: cmd_transcode/cmd_convert pass the file's
PARENT, cmd_auto passes the FILE — so the inference fired for one and never
for the other. The fix makes the single-file case an explicit flag on both
resolvers (recompressor pattern).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

TR_PATH = Path(__file__).resolve().parent.parent / "jxl_jpeg_transcoder.py"


def _tr():
    spec = importlib.util.spec_from_file_location("tr_item29", TR_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tr_item29"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def single_file_tree(tmp_path):
    # G:\lib\photo.jpg shape: the file sits at the root of its own folder,
    # so the modes-4/5 sibling output lands one level ABOVE that folder.
    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "photo.jpg"
    src.write_bytes(b"\xff\xd8" + b"\x00" * 16 + b"\xff\xd9")
    return src


def test_transcode_resolver_single_file_mode5_no_warning(single_file_tree, caplog):
    """cmd_transcode passes the file's PARENT as input_root: single-file
    mode 5 must not warn (pre-fix: warned on every run)."""
    tr = _tr()
    parent = single_file_tree.parent
    with caplog.at_level("WARNING"):
        tr.resolve_output_transcode(single_file_tree, 5, parent, decode=False,
                                    single_file=True)
    assert "Output outside input tree" not in caplog.text, caplog.text


def test_transcode_resolver_single_file_mode4_no_warning(single_file_tree, caplog):
    tr = _tr()
    parent = single_file_tree.parent
    with caplog.at_level("WARNING"):
        tr.resolve_output_transcode(single_file_tree, 4, parent, decode=False,
                                    single_file=True)
    assert "Output outside input tree" not in caplog.text, caplog.text


def test_transcode_resolver_cmd_auto_style_file_as_root_no_warning(single_file_tree, caplog):
    """cmd_auto passes the FILE itself as input_root: still no warning."""
    tr = _tr()
    with caplog.at_level("WARNING"):
        tr.resolve_output_transcode(single_file_tree, 5, single_file_tree,
                                    decode=False, single_file=True)
    assert "Output outside input tree" not in caplog.text, caplog.text


def test_transcode_resolver_folder_run_still_warns(tmp_path, caplog):
    """Guard against overreach: a FOLDER run whose mode-4 output escapes the
    tree keeps the warning (same warning the TIFF encoder/decoder surface)."""
    tr = _tr()
    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "photo.jpg"
    src.write_bytes(b"\xff\xd8" + b"\x00" * 16 + b"\xff\xd9")
    with caplog.at_level("WARNING"):
        tr.resolve_output_transcode(src, 4, lib, decode=False, single_file=False)
    assert "Output outside input tree" in caplog.text, caplog.text


def test_convert_resolver_single_file_mode5_no_warning(single_file_tree, caplog):
    """resolve_output_convert never got the first fix at all: a single-file
    mode-5 run anchored on output_root (the file's parent) and warned."""
    tr = _tr()
    parent = single_file_tree.parent
    with caplog.at_level("WARNING"):
        tr.resolve_output_convert(single_file_tree, 5, "converted", "",
                                  "jxl", "", "",
                                  output_root=parent, decode=False,
                                  single_file=True)
    assert "Output outside input tree" not in caplog.text, caplog.text


def test_convert_resolver_cmd_auto_style_file_as_root_no_warning(single_file_tree, caplog):
    tr = _tr()
    with caplog.at_level("WARNING"):
        tr.resolve_output_convert(single_file_tree, 5, "converted", "",
                                  "jxl", "", "",
                                  output_root=single_file_tree, decode=False,
                                  single_file=True)
    assert "Output outside input tree" not in caplog.text, caplog.text


def test_convert_resolver_folder_run_still_warns(tmp_path, caplog):
    tr = _tr()
    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "photo.jpg"
    src.write_bytes(b"\xff\xd8" + b"\x00" * 16 + b"\xff\xd9")
    with caplog.at_level("WARNING"):
        tr.resolve_output_convert(src, 5, "converted", "", "jxl", "", "",
                                  output_root=lib, decode=False,
                                  single_file=False)
    assert "Output outside input tree" in caplog.text, caplog.text
