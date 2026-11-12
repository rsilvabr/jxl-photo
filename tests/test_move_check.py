"""tools/move_check.py: a refactor that only MOVES a block into a new function
passes; every way a "move" can silently change behaviour is caught."""
import importlib.util
import sys
import textwrap
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "move_check.py"
_spec = importlib.util.spec_from_file_location("move_check_tool", _TOOL)
mc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mc      # dataclasses look the module up while it loads
_spec.loader.exec_module(mc)


BEFORE = textwrap.dedent('''
    import os

    LIMIT = 3


    def main(paths, flag):
        global LIMIT
        total = 0
        if flag:
            n = len(paths)
            for p in paths:
                total += os.path.getsize(p)
            LIMIT = n
        print(total)
        return total
''')

MOVED = textwrap.dedent('''
    def _sum_sizes(paths, total):
        """Moved out of main."""
        global LIMIT
        n = len(paths)
        for p in paths:
            total += os.path.getsize(p)
        LIMIT = n
        return total
''')

MAIN_AFTER = textwrap.dedent('''
    def main(paths, flag):
        global LIMIT
        total = 0
        if flag:
            total = _sum_sizes(paths, total)
        print(total)
        return total
''')

HEAD = "import os\n\nLIMIT = 3\n\n"


def _check(moved=MOVED, main_after=MAIN_AFTER, before=BEFORE, **kw):
    results = mc.check_moves({"m.py": before}, {"m.py": HEAD + moved + "\n" + main_after}, **kw)
    assert len(results) == 1
    return results[0]


def _errors(res):
    return " | ".join(res.errors)


def test_a_pure_move_is_proven():
    res = _check()
    assert res.ok, res.errors
    assert res.origin == "m.py::main"
    assert not res.notes


def test_a_changed_line_is_not_a_move():
    res = _check(moved=MOVED.replace("os.path.getsize(p)", "os.path.getsize(p) + 1"))
    assert not res.ok
    assert "no block" in _errors(res)


def test_a_variable_of_the_origin_that_is_not_passed_is_caught():
    res = _check(moved=MOVED.replace("_sum_sizes(paths, total)", "_sum_sizes(total)"),
                 main_after=MAIN_AFTER.replace("_sum_sizes(paths, total)", "_sum_sizes(total)"))
    assert "not parameters: paths" in _errors(res)


def test_an_accumulator_that_is_not_passed_is_caught():
    # `total += ...` reads total: without it as a parameter the moved path
    # raises UnboundLocalError, and only that path.
    res = _check(moved=MOVED.replace("_sum_sizes(paths, total)", "_sum_sizes(paths)"),
                 main_after=MAIN_AFTER.replace("_sum_sizes(paths, total)", "_sum_sizes(paths)"))
    assert "not parameters: total" in _errors(res)


def test_a_lost_global_declaration_is_caught():
    res = _check(moved=MOVED.replace("    global LIMIT\n", ""))
    assert "`global` in the origin" in _errors(res)
    assert "LIMIT" in _errors(res)


def test_a_value_the_origin_reads_after_the_block_must_come_back():
    res = _check(moved=MOVED.replace("    return total\n", ""),
                 main_after=MAIN_AFTER.replace("total = _sum_sizes(", "_sum_sizes("))
    assert "not returned: total" in _errors(res)


def test_a_return_inside_the_block_is_caught():
    before = BEFORE.replace("        n = len(paths)\n",
                            "        if not paths:\n            return -1\n        n = len(paths)\n")
    moved = MOVED.replace("    n = len(paths)\n",
                          "    if not paths:\n        return -1\n    n = len(paths)\n")
    res = _check(moved=moved, before=before)
    assert "has a `return`" in _errors(res)


def test_an_origin_that_changed_beyond_the_call_is_caught():
    res = _check(main_after=MAIN_AFTER.replace("print(total)", "print(total, flush=True)"))
    assert "changed beyond the call" in _errors(res)


def test_an_argument_that_is_not_the_same_variable_is_caught():
    res = _check(main_after=MAIN_AFTER.replace("_sum_sizes(paths, total)",
                                               "_sum_sizes(paths, total=0)"))
    assert not res.ok


def test_a_comment_change_is_a_note_not_an_error():
    res = _check(moved=MOVED.replace("    LIMIT = n\n", "    # the count\n    LIMIT = n\n"))
    assert res.ok, res.errors
    assert res.notes


def test_a_name_the_origin_used_only_before_the_block_is_not_flagged():
    # `p` is a loop variable before the block and inside it: nothing after
    # the call reads the block's `p`, so it need not come back.
    before = BEFORE.replace("    total = 0\n", "    for p in paths:\n        print(p)\n    total = 0\n")
    main_after = MAIN_AFTER.replace("    total = 0\n", "    for p in paths:\n        print(p)\n    total = 0\n")
    res = _check(before=before, main_after=main_after)
    assert res.ok, res.errors


def test_a_value_read_on_the_next_loop_turn_must_come_back():
    before = textwrap.dedent('''
        def main(paths):
            last = None
            for p in paths:
                print(last)
                last = p
                n = len(p)
            return 0
    ''')
    moved = textwrap.dedent('''
        def _step(p):
            last = p
            n = len(p)
            return n
    ''')
    main_after = textwrap.dedent('''
        def main(paths):
            last = None
            for p in paths:
                print(last)
                n = _step(p)
            return 0
    ''')
    res = mc.check_moves({"m.py": before}, {"m.py": moved + "\n" + main_after})[0]
    assert "read by the origin after it, but not returned: last" in _errors(res)


def test_passing_a_name_the_origin_does_not_define_is_caught():
    moved = MOVED.replace("_sum_sizes(paths, total)", "_sum_sizes(paths, total, ghost)")
    res = _check(moved=moved,
                 main_after=MAIN_AFTER.replace("_sum_sizes(paths, total)",
                                               "_sum_sizes(paths, total, ghost)"))
    assert "does not define: ghost" in _errors(res)


def test_two_identical_copies_into_one_function_check_both_callers():
    before = textwrap.dedent('''
        def a(xs):
            total = 0
            for x in xs:
                total += x
            print(total)


        def b(xs):
            total = 0
            for x in xs:
                total += x
            print(total, "b")
    ''')
    moved = textwrap.dedent('''
        def _sum(xs):
            total = 0
            for x in xs:
                total += x
            return total
    ''')
    callers = textwrap.dedent('''
        def a(xs):
            total = _sum(xs)
            print(total)


        def b(xs):
            total = _sum(xs)
            print(total, "b")
    ''')
    res = mc.check_moves({"m.py": before}, {"m.py": moved + callers})[0]
    assert res.ok, res.errors
    # a change in the SECOND caller (not the origin the tool picked) is caught
    bad = callers.replace('print(total, "b")', 'print(total, "B")')
    res = mc.check_moves({"m.py": before}, {"m.py": moved + bad})[0]
    assert "b changed beyond the call" in _errors(res)


def test_no_new_function_means_nothing_to_check():
    assert mc.check_moves({"m.py": BEFORE}, {"m.py": BEFORE}) == []
