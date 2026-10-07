#!/usr/bin/env python3
"""The docs agree with themselves and with the code.

Three drifts found on 2026-10-07, each invisible until someone clicks or
compares:

  * a README link pointed at a heading that had been renamed (a dead anchor);
  * the README's Acknowledgments credited the current AI tools while all five
    script READMEs still credited the old ones;
  * the wrapper README's per-script settings tables are hand-copied defaults.

Links and anchors are checked the way GitHub renders them. The local release
drafts (docs/RELEASE_*.md, gitignored) and deprecated/ are out of scope.
"""

import ast
import functools
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = ([REPO / "README.md", REPO / "AGENTS.md"]
        + sorted(p for p in (REPO / "docs").glob("*.md")
                 if not p.name.startswith("RELEASE_")))
SCRIPT_READMES = ["README_jxl_tiff_encoder.md", "README_jxl_tiff_decoder.md",
                  "README_jxl_jpeg_transcoder.md", "README_jxl_recompressor.md",
                  "README_jxl_tools.md"]


def _prose_lines(path):
    """(line number, text) outside fenced code blocks."""
    in_code = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            yield i, line


def _slug(heading):
    t = heading.strip().lower()
    t = re.sub(r"[^\w\- ]", "", t)
    return t.replace(" ", "-")


@functools.lru_cache(maxsize=None)
def _anchors(path):
    out, seen = set(), {}
    for _i, line in _prose_lines(path):
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            s = _slug(m.group(1))
            n = seen.get(s, 0)
            out.add(s if n == 0 else f"{s}-{n}")
            seen[s] = n + 1
        out.update(re.findall(r'<a (?:name|id)="([^"]+)"', line))
    return frozenset(out)


def test_internal_links_and_anchors_resolve():
    broken = []
    for doc in DOCS:
        for i, line in _prose_lines(doc):
            for target in re.findall(r"\]\(([^)\s]+)\)", line):
                if re.match(r"^(https?:|mailto:)", target):
                    continue
                path, _, frag = target.partition("#")
                dest = (doc.parent / path).resolve() if path else doc.resolve()
                rel = doc.relative_to(REPO)
                if path and not dest.exists():
                    broken.append(f"{rel}:{i}: no file {target}")
                elif frag and dest.suffix == ".md" and frag not in _anchors(dest):
                    broken.append(f"{rel}:{i}: no heading for {target}")
    assert not broken, "broken links:\n" + "\n".join(broken)


def _ai_credit(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index("## Acknowledgments")
    hits = [l.strip() for l in lines[start:] if "for code assistance" in l]
    assert len(hits) == 1, f"{path.name}: expected one AI-credit line, got {hits}"
    return hits[0]


def test_acknowledgments_credit_the_same_ai_tools():
    want = _ai_credit(REPO / "README.md")
    for name in SCRIPT_READMES:
        assert _ai_credit(REPO / "docs" / name) == want, (
            f"docs/{name} Acknowledgments differ from README.md's:\n  {want}")


@functools.lru_cache(maxsize=None)
def _settings(script):
    tree = ast.parse((REPO / script).read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.isupper()):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def test_wrapper_readme_settings_tables_match_the_code():
    doc = REPO / "docs" / "README_jxl_tools.md"
    script, wrong, checked = None, [], 0
    for i, line in _prose_lines(doc):
        m = re.match(r"^#{2,5}\s*`?(jxl_\w+\.py)`?\s*$", line)
        if m:
            script = m.group(1)
            continue
        m = re.match(r"^\|\s*`([A-Z][A-Z0-9_]{3,})`\s*\|\s*`([^`]+)`", line)
        if not (m and script):
            continue
        name, shown = m.groups()
        settings = _settings(script)
        if name not in settings:
            wrong.append(f"line {i}: {name} is not a setting of {script}")
            continue
        checked += 1
        try:
            value = ast.literal_eval(shown)
        except (ValueError, SyntaxError):
            value = shown
        if value != settings[name]:
            wrong.append(f"line {i}: {script} {name} = {settings[name]!r}, "
                         f"the table says {shown}")
    assert checked > 40, "the settings tables were not found"
    assert not wrong, "README_jxl_tools.md settings tables:\n" + "\n".join(wrong)
