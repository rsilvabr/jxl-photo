"""pyflakes finds nothing in the six files (refactor F4.8).

It catches what the suite only meets on a rare path: an undefined name (a
helper moved to jxl_photo_common.py that still reads a script global), an
import or a variable nobody uses (a leftover of a move), an f-string without
a placeholder. Skipped when pyflakes is not installed.

Two kinds of message are deliberate:
- "imported but unused" on a line that says `# noqa: F401`: a probe that
  imports a module only to see that it is installed (and, in the wrapper, so
  that PyInstaller bundles it into the exe);
- the entries of ALLOWED, each with its reason. An entry nothing matches any
  more fails too: drop it.
"""
import io
from pathlib import Path

import pytest

pytest.importorskip("pyflakes")
from pyflakes import api, reporter  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FILES = ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py",
         "jxl_recompressor.py", "jxl_photo.py", "jxl_photo_common.py")

# (file, start of the message): why it stays
ALLOWED = {
    ("jxl_photo.py", "redefinition of unused 'resolver'"):
        "the collision scan's resolver is None until a child module is imported; "
        "the nested def replaces that default only on that branch",
    ("jxl_photo.py", "redefinition of unused '_skip_check'"):
        "same shape: None unless the child is the encoder",
}


class _Collect(reporter.Reporter):
    def __init__(self):
        super().__init__(io.StringIO(), io.StringIO())
        self.messages = []

    def flake(self, message):
        self.messages.append(message)

    def syntaxError(self, filename, msg, lineno, offset, text):
        self.messages.append(f"syntax error: {msg} (line {lineno})")


def _messages():
    out = []
    for name in FILES:
        src = (REPO / name).read_text(encoding="utf-8")
        lines = src.splitlines()
        col = _Collect()
        api.check(src, name, col)
        for m in col.messages:
            if isinstance(m, str):
                out.append((name, 0, m, ""))
                continue
            out.append((name, m.lineno, m.message % m.message_args, lines[m.lineno - 1]))
    return out


MESSAGES = _messages()


def test_pyflakes_finds_nothing():
    bad = []
    for name, lineno, text, line in MESSAGES:
        if "imported but unused" in text and "# noqa: F401" in line:
            continue
        if any(f == name and text.startswith(start) for f, start in ALLOWED):
            continue
        bad.append(f"{name}:{lineno}: {text}")
    assert not bad, "pyflakes:\n  " + "\n  ".join(bad)


def test_every_allowed_message_still_happens():
    stale = [key for key in ALLOWED
             if not any(name == key[0] and text.startswith(key[1])
                        for name, _l, text, _line in MESSAGES)]
    assert not stale, f"ALLOWED entries nothing matches any more (drop them): {stale}"
