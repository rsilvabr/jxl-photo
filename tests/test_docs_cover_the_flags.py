#!/usr/bin/env python3
"""Every CLI flag must appear in its script's README.

The v2.0.0 doc pass started from an ad-hoc script that compared argparse against
the READMEs, and it found ten flags nobody had documented — including
`--summary-json`, which is the contract other tools are supposed to consume.
Finding that by hand once is luck; this makes it a test.

Deliberately shallow: it checks that the flag STRING is somewhere in the file,
not that the prose is any good. A flag can be documented badly and still pass —
but it can no longer be added with no mention at all, which is the failure mode
this actually catches.
"""

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

PAIRS = [
    ("jxl_tiff_encoder.py", "docs/README_jxl_tiff_encoder.md"),
    ("jxl_tiff_decoder.py", "docs/README_jxl_tiff_decoder.md"),
    ("jxl_jpeg_transcoder.py", "docs/README_jxl_jpeg_transcoder.md"),
    ("jxl_photo.py", "docs/README_jxl_tools.md"),
]


def _flags(script: str) -> list[str]:
    """Every `--flag` passed to an add_argument() call in the script."""
    tree = ast.parse((REPO / script).read_text(encoding="utf-8", errors="replace"))
    found = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            for arg in node.args:
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        and arg.value.startswith("--")):
                    found.add(arg.value)
    return sorted(found)


@pytest.mark.parametrize("script,doc", PAIRS, ids=[s for s, _ in PAIRS])
def test_every_flag_is_documented(script: str, doc: str) -> None:
    text = (REPO / doc).read_text(encoding="utf-8", errors="replace")
    missing = [f for f in _flags(script) if f not in text]
    assert not missing, (
        f"{doc} does not mention: {', '.join(missing)}\n"
        f"Add them to the flag list (or, if one is deliberately internal, say so "
        f"there — this test only checks that the string appears)."
    )


@pytest.mark.parametrize("script,doc", PAIRS, ids=[s for s, _ in PAIRS])
def test_the_readme_does_not_invent_flags(script: str, doc: str) -> None:
    """The other direction: a flag documented but removed from the script leaves
    a README promising something that now exits 2 at argparse."""
    import re
    text = (REPO / doc).read_text(encoding="utf-8", errors="replace")
    # The trailing [\w] matters: without it the pattern stops at the underscore
    # and reports `--lossless_jpeg=1` as a bogus `--lossless`, `--reconstruct_jpeg`
    # as `--reconstruct`, and so on. Every "invented flag" this test found on its
    # first run was that truncation, not a real stale doc.
    documented = set(re.findall(r"(?<![\w-])--[a-z][\w-]{2,}", text))
    # Flags any script in this repo accepts: the READMEs legitimately reference
    # a sibling script's options (the encoder's docs name the decoder's).
    ours = {f for s, _ in PAIRS for f in _flags(s)}
    # Options belonging to the external tools we invoke or install, which appear
    # in setup examples and in explanations of what we pass to cjxl/djxl.
    external = {"--version", "--quiet", "--upgrade", "--user", "--no-cache-dir",
                "--color_space", "--lossless_jpeg", "--bits_per_sample",
                "--num_threads", "--overwrite_original", "--reconstruct_jpeg",
                "--container", "--modular", "--jpeg_quality", "--pixels_to_jpeg"}
    # Named in prose ABOUT their own removal — a changelog cannot stop
    # mentioning a flag just because it no longer exists.
    historical = {"--resize"}
    invented = sorted(documented - ours - external - historical)
    assert not invented, (
        f"{doc} documents flags that no script in this repo accepts: "
        f"{', '.join(invented)}"
    )
