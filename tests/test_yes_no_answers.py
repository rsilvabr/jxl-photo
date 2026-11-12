"""One reading of a yes/no answer in both screens of the wizard (#649).

The plain screen read any answer not starting with "n" as YES on a [Y/n]
question -- "sair" (Portuguese for "leave") at "Run this job? [Y/n]" ran the
job -- and "sim" as NO in confirm(); the Rich screen asked again on "yes" and
"sim". Now both take y/yes/s/sim and n/no/nao/não, Enter is the default, and
anything else asks again. Deleting still needs the typed HHMM token.
"""
import builtins
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jxl_photo as wp  # noqa: E402


def _feed(monkeypatch, answers):
    """input() answers from a list; returns the list of questions asked."""
    pending = list(answers)
    asked = []

    def fake_input(prompt=""):
        asked.append(prompt)
        if not pending:
            raise EOFError
        return pending.pop(0)
    monkeypatch.setattr(builtins, "input", fake_input)
    return asked


def _ui(kind):
    if kind == "rich":
        if not wp.RICH_AVAILABLE:
            pytest.skip("rich is not installed")
        return wp.RichUI()
    return wp.PlainUI()


def _ask(ui, method, default):
    if method == "confirm":
        return ui.confirm("t.run", "Run this job?", default=default)
    return ui.ask_yes("t.run", "Run this job?", "Run this job? [Y/n]: " if default
                      else "Run this job? [y/N]: ", default=default)


CASES = [
    # answers, default, result, questions asked
    (["sair", "s"], True, True, 2),       # not yes/no: asked again (it ran the job)
    (["sair", "n"], True, False, 2),
    (["x", ""], False, False, 2),
    (["sim"], False, True, 1),
    (["SIM"], True, True, 1),
    (["s"], False, True, 1),
    (["não"], True, False, 1),
    (["NAO"], True, False, 1),
    (["yes"], False, True, 1),
    (["no"], True, False, 1),
    (["y"], False, True, 1),
    (["n"], True, False, 1),
    ([""], True, True, 1),
    ([""], False, False, 1),
]


@pytest.mark.parametrize("kind", ["plain", "rich"])
@pytest.mark.parametrize("method", ["confirm", "ask_yes"])
@pytest.mark.parametrize("answers,default,result,asks", CASES,
                         ids=[f"{'+'.join(a) or 'Enter'}-default{d}" for a, d, _r, _n in CASES])
def test_one_reading_of_yes_and_no(monkeypatch, kind, method, answers, default, result, asks):
    ui = _ui(kind)
    asked = _feed(monkeypatch, answers)
    assert _ask(ui, method, default) is result
    assert len(asked) == asks


@pytest.mark.parametrize("kind", ["plain", "rich"])
@pytest.mark.parametrize("method", ["confirm", "ask_yes"])
def test_the_end_of_input_still_reaches_the_caller(monkeypatch, kind, method):
    """A closed stdin is handled where it always was (the caller), not read as
    an answer."""
    ui = _ui(kind)
    _feed(monkeypatch, [])
    with pytest.raises(EOFError):
        _ask(ui, method, True)


def test_the_reading_itself():
    for text in ("y", "yes", "s", "sim", " Sim ", "YES"):
        assert wp._yes_no(text, False) is True, text
    for text in ("n", "no", "nao", "não", "NÃO", " No "):
        assert wp._yes_no(text, True) is False, text
    assert wp._yes_no("", True) is True
    assert wp._yes_no("  ", False) is False
    for text in ("sair", "q", "ok", "1", "yy", "nope"):
        assert wp._yes_no(text, True) is None, text
