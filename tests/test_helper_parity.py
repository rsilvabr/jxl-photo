"""The four scripts deliberately carry their own copy of a few helpers, so each
stays standalone (see AGENTS.md). The cost of that choice is a rule nobody can
enforce by reading: a bug fixed in one copy has to be fixed in all of them.

This test enforces it. Each helper is compared across every script that defines
it, on normalised AST — docstrings dropped, comments and formatting invisible to
`ast`, and local names alpha-renamed. What survives that is the logic itself, so
the test stays quiet about cosmetic differences (the copies already differ in a
loop variable name and a docstring line) and fails only when one copy actually
starts behaving differently from the others.

When it fails: apply the same fix to every copy listed, or, if the difference is
deliberate, drop the helper from SHARED_HELPERS with a comment saying why.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

SCRIPTS = [
    "jxl_photo.py",
    "jxl_tiff_encoder.py",
    "jxl_tiff_decoder.py",
    "jxl_jpeg_transcoder.py",
]

# Helpers duplicated on purpose. Not every script defines every one — the test
# only compares the copies that exist, and requires at least two.
SHARED_HELPERS = [
    "_marker_matches",
    "_replace_suffix_token",
    "_is_relative_to",
    "_abort_on_duplicate_outputs",
    "_run_exiftool_argfile",
    "_tool_version",
    # Disk-full abort. These decide whether a run stops or grinds on, so a copy
    # drifting would make one backend keep failing file after file.
    "_reset_abort",
    "_aborted",
    "_signal_abort",
    "_abort_if_disk_full",
    # Directory-scan progress. A copy drifting would leave one backend silent
    # through the exact 25-second pause that looks like a freeze.
    "_scan_state",
    "_scan_tick",
    "_scan_done",
]

# Two helpers are semantically equivalent across their copies but structurally
# different, so normalisation cannot fold them together:
#
#   _replace_suffix_token       jxl_photo.py tests `A and not B`, the backends
#                               assign `left_ok = not A or B` first. De Morgan —
#                               same result, different shape.
#   _abort_on_duplicate_outputs the encoder/decoder reuse one loop name where
#                               the transcoder uses two.
#
# Semantic equivalence is undecidable in general, so instead of asserting these
# agree, their current normalised forms are pinned. Any edit to any copy flips a
# hash and fails the test — which is the point: you then re-check by hand that
# the copies still behave alike and update the baseline in the same commit.
PINNED_VARIANTS: dict[str, dict[str, str]] = {
    "_replace_suffix_token": {
        "jxl_photo.py": "e38c82ab6d42c9ac",
        "jxl_tiff_encoder.py": "5608038ca7681acb",
        "jxl_tiff_decoder.py": "5608038ca7681acb",
        "jxl_jpeg_transcoder.py": "5608038ca7681acb",
    },
    "_abort_on_duplicate_outputs": {
        "jxl_tiff_encoder.py": "a80bdc7535cacaad",
        "jxl_tiff_decoder.py": "a80bdc7535cacaad",
        "jxl_jpeg_transcoder.py": "2125e8023e27b0bf",
    },
}


def _bound_names(fn: ast.FunctionDef) -> set[str]:
    """Names bound inside the function: arguments, assignments, loop and
    comprehension targets, `with ... as`, `except ... as`.

    Only these get renamed. Globals and attributes (`os`, `Counter`, `re`) keep
    their names, so swapping one module for another still fails the test.
    """
    bound: set[str] = set()
    args = fn.args
    for group in (args.posonlyargs, args.args, args.kwonlyargs):
        bound.update(a.arg for a in group)
    for extra in (args.vararg, args.kwarg):
        if extra is not None:
            bound.add(extra.arg)

    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # `import re as _re` inside one copy and a module-level `re` in
            # another is a naming difference, not a logic difference.
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
    return bound


class _Canonicalise(ast.NodeTransformer):
    """Rename bound local names to v0, v1, ... in order of first appearance."""

    def __init__(self, bound: set[str]) -> None:
        self._bound = bound
        self._mapping: dict[str, str] = {}

    def _rename(self, name: str) -> str:
        if name not in self._bound:
            return name
        if name not in self._mapping:
            self._mapping[name] = f"v{len(self._mapping)}"
        return self._mapping[name]

    def visit_Name(self, node: ast.Name) -> ast.AST:
        node.id = self._rename(node.id)
        return self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.arg = self._rename(node.arg)
        node.annotation = None  # annotations are documentation, not logic
        return self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.AST:
        if node.name:
            node.name = self._rename(node.name)
        return self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> ast.AST:
        if node.asname:
            node.asname = self._rename(node.asname)
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        # Only the value side may be a renamed local; the attribute name is API.
        node.value = self.visit(node.value)
        return node


def _normalise(fn: ast.FunctionDef) -> str:
    fn = ast.parse(ast.unparse(fn)).body[0]  # detach from the original tree
    assert isinstance(fn, ast.FunctionDef)

    body = fn.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # docstring is prose, not logic
    if not body:
        return "<empty>"

    fn.body = body
    fn.name = "_"           # the name is already the grouping key
    fn.returns = None       # return annotation is documentation
    fn.decorator_list = []  # @staticmethod vs bare function is a call-site detail

    canonical = _Canonicalise(_bound_names(fn)).visit(fn)
    ast.fix_missing_locations(canonical)
    return ast.dump(canonical, annotate_fields=False)


def _collect() -> dict[str, dict[str, str]]:
    """{helper name: {script: normalised source}}"""
    found: dict[str, dict[str, str]] = {name: {} for name in SHARED_HELPERS}
    for script in SCRIPTS:
        path = REPO / script
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in found:
                found[node.name][script] = _normalise(node)
    return found


COPIES = _collect()


@pytest.mark.parametrize("helper", sorted(PINNED_VARIANTS))
def test_known_equivalent_variants_are_unchanged(helper: str) -> None:
    """These copies are equivalent but not structurally identical, so their
    shapes are pinned instead of compared. A flipped hash means someone edited
    one copy: re-check the others by hand, then update PINNED_VARIANTS."""
    expected = PINNED_VARIANTS[helper]
    actual = {
        script: hashlib.sha256(norm.encode()).hexdigest()[:16]
        for script, norm in COPIES[helper].items()
    }
    assert actual == expected, (
        f"{helper}() changed in a pinned copy.\n"
        f"  expected: {expected}\n"
        f"  actual:   {actual}\n"
        f"Confirm every copy still behaves the same, then update "
        f"PINNED_VARIANTS in this file."
    )


@pytest.mark.parametrize(
    "helper", [h for h in SHARED_HELPERS if h not in PINNED_VARIANTS]
)
def test_duplicated_helper_copies_agree(helper: str) -> None:
    copies = COPIES[helper]
    assert len(copies) >= 2, (
        f"{helper} was found in {len(copies)} script(s). If it stopped being "
        f"duplicated, remove it from SHARED_HELPERS; if it was renamed, update "
        f"the list."
    )

    variants: dict[str, list[str]] = {}
    for script, normalised in copies.items():
        variants.setdefault(normalised, []).append(script)

    if len(variants) > 1:
        groups = "\n".join(
            f"  variant {i}: {', '.join(sorted(scripts))}"
            for i, scripts in enumerate(variants.values(), 1)
        )
        pytest.fail(
            f"{helper}() has drifted between its copies — the logic differs, "
            f"not just names or comments.\n{groups}\n"
            f"Fix every copy, or drop it from SHARED_HELPERS if the difference "
            f"is deliberate."
        )


def test_every_listed_helper_is_actually_duplicated() -> None:
    """Guards the list itself: an entry nobody defines any more would make
    test_duplicated_helper_copies_agree vacuous instead of failing."""
    missing = [name for name, copies in COPIES.items() if not copies]
    assert not missing, (
        f"SHARED_HELPERS lists helpers that no script defines: {missing}. "
        f"They were renamed or removed — update the list."
    )
