"""The wrapper asks the child's own parser about the expert flags (refactor
A7, F4.6).

_flags_request_delete and _flags_set_mode used to imitate argparse by hand
(abbreviations, spellings, `=value` on a store_true flag); six bugs of the
tracker were that copy drifting from the real thing (#210, #263, #457, #518,
#603, #604). With the direction known they now read the child's build_parser()
parse (_child_flags); the token reading stays only as the fallback when the
child cannot be imported, where it errs towards "this deletes".

One table, both implementations: CHILD is what the child's argparse does,
TOKENS what the fallback answers. They differ only where the fallback is
deliberately cautious.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp  # noqa: E402

DIRECTIONS = [("tiff", "jxl"), ("jxl", "tiff"), ("jpeg", "jxl"), ("jxl", "jxl")]

# flags, CHILD (the child's own parse), TOKENS (the fallback)
DELETE_CASES = [
    ("--delete-source", True, True),
    ("--delete-source --delete-confirm-off", True, True),
    ('--staging "E:\\my dir [x]" --delete-source', True, True),
    ("--delete-so", True, True),                     # argparse abbreviation
    ("--delete-so --delete-c", True, True),
    ("--delete-skipped", False, False),
    ("--delete-sk", False, False),
    ("--delete-confirm-off", False, False),
    ("--delete-s", False, False),                    # ambiguous: rejected
    ("--delete-source=1", False, False),             # store_true takes no value
    ("--delete-source-never", False, False),
    ("--staging E:\\temp", False, False),
    ("", False, False),
    (None, False, False),
    # No such spelling in any child: argparse leaves it unknown (and the
    # parse gate refuses the run). The fallback cannot know and says delete.
    ("--delete_so", False, True),
    ("--delete_source", False, True),
]

MODE_CASES = [
    ("--mode 8", "--mode", "--mode"),
    ("--mode=8", "--mode=8", "--mode=8"),
    ("--mod 8", "--mod", "--mod"),
    ("--staging E:\\temp", None, None),
    ("", None, None),
    (None, None, None),
]


@pytest.mark.parametrize("origin,dest", DIRECTIONS)
@pytest.mark.parametrize("flags,child,_tokens", DELETE_CASES)
def test_delete_request_is_the_child_s_answer(origin, dest, flags, child, _tokens):
    assert wp._flags_request_delete(flags, origin, dest) is child


@pytest.mark.parametrize("flags,_child,tokens", DELETE_CASES)
def test_delete_request_fallback_reads_the_tokens(flags, _child, tokens):
    assert wp._flags_request_delete(flags) is tokens


@pytest.mark.parametrize("origin,dest", DIRECTIONS)
@pytest.mark.parametrize("flags,child,_tokens", MODE_CASES)
def test_mode_is_the_child_s_answer(origin, dest, flags, child, _tokens):
    assert wp._flags_set_mode(flags, origin, dest) == child


@pytest.mark.parametrize("flags,_child,tokens", MODE_CASES)
def test_mode_fallback_reads_the_tokens(flags, _child, tokens):
    assert wp._flags_set_mode(flags) == tokens


def test_the_encoder_s_modular_flag_is_not_a_mode():
    assert wp._flags_set_mode("--modular on --effort 9", "tiff", "jxl") is None


def test_a_child_that_cannot_be_imported_falls_back_closed(monkeypatch):
    """No parser to ask: the tokens decide, and an unknown delete spelling
    still counts as a delete (the HHMM gate is asked rather than skipped)."""
    def broken(name, *a, **k):
        raise ImportError(name)
    monkeypatch.setattr(wp.importlib, "import_module", broken)
    assert wp._child_flags("tiff", "jxl", "--delete-source") is None
    assert wp._flags_request_delete("--delete_so", "tiff", "jxl") is True
    assert wp._flags_set_mode("--mode 8", "tiff", "jxl") == "--mode"


def test_the_probe_leaves_the_parser_s_mode_default_alone():
    """set_defaults on the probe's own parser: the child's real default (the
    one a run gets) is untouched."""
    import jxl_tiff_encoder as enc
    assert wp._flags_set_mode("--effort 9", "tiff", "jxl") is None
    assert enc.build_parser().get_default("mode") == 0


def test_every_caller_gives_the_direction():
    """Without the direction the fallback answers: a caller that dropped it
    would silently go back to imitating argparse."""
    import ast
    tree = ast.parse(Path(wp.__file__).read_text(encoding="utf-8"))
    needs = {"_flags_request_delete": 3, "_flags_set_mode": 3, "_append_expert_flags": 4}
    short = [f"{n.func.id} at line {n.lineno}" for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in needs and len(n.args) + len(n.keywords) < needs[n.func.id]]
    assert not short, short


def test_a_delete_in_the_flags_adds_the_confirm_off_for_the_child():
    cmd = []
    wp._append_expert_flags(cmd, "--delete-so", "tiff", "jxl")
    assert cmd == ["--delete-so", "--delete-confirm-off"]
    cmd = []
    wp._append_expert_flags(cmd, "--delete-skipped", "tiff", "jxl")
    assert cmd == ["--delete-skipped"]
