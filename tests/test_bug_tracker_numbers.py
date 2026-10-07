#!/usr/bin/env python3
"""The bug tracker's numbering stays usable.

docs/bug_tracking_since_v1.0.md and docs/bug_tracking_archive.md share ONE
sequence of bug numbers. On 2026-10-07 fifteen numbers (#195-#209) turned out
to be used twice — three sections had restarted the count at #195 — and the
rounds were out of date order. These tests pin the rules the tracker's header
states:

  * every numbered table row in the two files has its own number;
  * the header's "Next free number: #N" is the highest number + 1;
  * the main file's sections run newest first;
  * the index table lists every section, with a working anchor and the
    numbers that section really holds.
"""

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
MAIN = DOCS / "bug_tracking_since_v1.0.md"
ARCHIVE = DOCS / "bug_tracking_archive.md"

ROW = re.compile(r"^\|\s*(\d+)\s*\|")
DATE = re.compile(r"\((\d{4})-(\d{2})(?:-(\d{2}))?\)\s*$")


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def _sections(lines):
    """[(heading, [lines])] for every level-2 heading."""
    out = []
    for line in lines:
        if line.startswith("## "):
            out.append((line, []))
        elif out:
            out[-1][1].append(line)
    return out


def _numbers(lines):
    return [int(m.group(1)) for m in map(ROW.match, lines) if m]


def _slug(heading):
    t = heading.lstrip("#").strip().lower()
    t = re.sub(r"[^\w\- ]", "", t)
    return t.replace(" ", "-")


def _ranges(ns):
    out, start, prev = [], None, None
    for n in sorted(ns):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append((start, prev))
            start = prev = n
    if start is not None:
        out.append((start, prev))
    return ", ".join(f"#{a}" if a == b else f"#{a}–#{b}" for a, b in out)


def test_every_bug_number_is_unique():
    seen = {}
    dups = []
    for path in (MAIN, ARCHIVE):
        for i, line in enumerate(_lines(path), 1):
            m = ROW.match(line)
            if m:
                n = int(m.group(1))
                if n in seen:
                    dups.append(f"#{n}: {seen[n]} and {path.name}:{i}")
                else:
                    seen[n] = f"{path.name}:{i}"
    assert not dups, "bug numbers used twice:\n" + "\n".join(dups)


def test_next_free_number_is_current():
    text = MAIN.read_text(encoding="utf-8")
    m = re.search(r"Next free number: #(\d+)", text)
    assert m, "the header lost its 'Next free number: #N' line"
    highest = max(_numbers(_lines(MAIN)) + _numbers(_lines(ARCHIVE)))
    assert int(m.group(1)) == highest + 1, (
        f"'Next free number' says #{m.group(1)}, but the highest number in use "
        f"is #{highest}: write #{highest + 1}")


def test_sections_run_newest_first():
    keys = []
    for heading, _body in _sections(_lines(MAIN)):
        m = DATE.search(heading)
        assert m, f"section heading without a (YYYY-MM-DD) date: {heading}"
        keys.append(((int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)),
                     heading))
    for (newer, h1), (older, h2) in zip(keys, keys[1:]):
        assert newer >= older, f"out of date order:\n  {h1}\n  above\n  {h2}"


def test_index_matches_the_sections():
    lines = _lines(MAIN)
    sections = _sections(lines)
    index = {}
    for line in lines:
        m = re.match(r"^\| \[[^\]]+\]\(#([^)]+)\) \|.*\| ([^|]+) \|$", line)
        if m:
            index[m.group(1)] = m.group(2).strip()
    for heading, body in sections:
        anchor = _slug(heading)
        assert anchor in index, f"the index has no row linking to #{anchor}"
        ns = _numbers(body)
        want = f"{_ranges(ns)} ({len(ns)})" if ns else "—"
        assert index[anchor] == want, (
            f"index row for '{heading}' says '{index[anchor]}', "
            f"the section holds '{want}'")
    assert len(index) == len(sections), "the index links to a missing section"
