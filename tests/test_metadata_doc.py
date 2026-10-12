#!/usr/bin/env python3
"""docs/metadata_written.md must list every jxlphoto-* token the scripts write.

That page is the answer to "what does this tool put into my photos?". A token
added to a script without a line there makes the answer silently incomplete,
so this fails the moment a script carries a `jxlphoto-<name>` literal the page
does not mention.

Deliberately shallow, like test_docs_cover_the_flags: it checks that the token
NAME is on the page, not that the prose is right.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "metadata_written.md"
SCRIPTS = [
    "jxl_tiff_encoder.py",
    "jxl_tiff_decoder.py",
    "jxl_jpeg_transcoder.py",
    "jxl_recompressor.py",
    "jxl_photo.py",
]
# A token literal in the source: "jxlphoto-src:", "jxlphoto-grayscale",
# "jxlphoto-icc:inherited". The bare namespace "jxlphoto-" is not a token.
_TOKEN_RE = re.compile(r"""["']jxlphoto-([a-z]+)""")


def _tokens() -> set:
    found = set()
    for name in SCRIPTS:
        text = (REPO / name).read_text(encoding="utf-8", errors="replace")
        found.update(_TOKEN_RE.findall(text))
    return found


def test_the_scripts_still_write_tokens():
    # Guards the regex: if it stopped matching, the test below would pass
    # vacuously.
    assert {"src", "srcsum", "origin", "derived", "mpg"} <= _tokens()


@pytest.mark.parametrize("token", sorted(_tokens()))
def test_every_token_is_documented(token):
    doc = DOC.read_text(encoding="utf-8")
    assert f"jxlphoto-{token}" in doc, (
        f"jxlphoto-{token} is written by a script but missing from "
        f"docs/metadata_written.md")


@pytest.mark.parametrize("field", ["ICC:<base64>", "gen=", "XMP-dc:Relation",
                                   "XMP-xmp:CreatorTool", "EXIF:Software",
                                   "xmpMM:History"])
def test_the_non_token_fields_are_documented(field):
    assert field in DOC.read_text(encoding="utf-8")


def test_the_readme_links_the_page():
    assert "docs/metadata_written.md" in (REPO / "README.md").read_text(encoding="utf-8")
