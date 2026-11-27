"""The main thread's waits for a pool (_as_completed, common): M1 of the
second review of 261127_Fable_plan_pending-code.

_as_completed is concurrent.futures.as_completed read in waits of _WAIT_TICK
(a Ctrl+C handler runs between them). Its first version called
concurrent.futures.wait over every pending future at each wake-up: O(pending)
work per completed file, so a re-run of 20 000 up-to-date JPEGs took 126 s
instead of 8. The test below counts that work instead of timing it: every
read of a future's state is counted, and the total must stay linear.

Pre-fix proof: copy jxl_photo_common.py of the version to test into a
folder, then
    set JXLPHOTO_SCRIPTS_UNDER_TEST=<that folder>
    python -m pytest tests/test_wait_ticks.py -q
"""

import concurrent.futures as cf
import importlib.util
import os
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)


def _load_common():
    spec = importlib.util.spec_from_file_location(
        "jxl_photo_common_wait_ticks", SCRIPTS / "jxl_photo_common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


common = _load_common()


class _CountingFuture(cf.Future):
    """A Future whose every read of its state is counted: the work a waiter
    does on it (concurrent.futures.wait reads the state of every future it is
    given, each time)."""
    reads = [0]

    @property
    def _state(self):
        _CountingFuture.reads[0] += 1
        return self.__dict__["_counted_state"]

    @_state.setter
    def _state(self, value):
        self.__dict__["_counted_state"] = value


def test_as_completed_does_not_walk_the_pending_futures():
    """N futures finishing one at a time: the work must be O(N), not
    O(N * pending). Before the fix: about N*N/2 state reads (2 000 000 for
    N = 2000); with one callback per future, a few per future."""
    n = 2000
    futs = [_CountingFuture() for _ in range(n)]
    for f in futs:
        assert f.set_running_or_notify_cancel()
    _CountingFuture.reads[0] = 0
    gen = common._as_completed(futs)
    order = []
    for i, f in enumerate(futs):
        f.set_result(i)
        order.append(next(gen))
    with pytest.raises(StopIteration):
        next(gen)
    assert order == futs
    assert _CountingFuture.reads[0] <= 20 * n, _CountingFuture.reads[0]


def test_as_completed_yields_each_future_once_in_completion_order():
    """Each future once (one passed twice too), in the order they finish —
    one raises (its result() raises the task's own exception), one is
    cancelled before it starts; an empty list ends at once."""
    assert list(common._as_completed([])) == []
    futs = [cf.Future() for _ in range(5)]
    for i, f in enumerate(futs):
        if i != 3:
            assert f.set_running_or_notify_cancel()

    def finish():
        # Start once the waiter has hooked every future (with the fix:
        # one done-callback each); 5 s at most for any other waiter.
        until = time.monotonic() + 5
        while (time.monotonic() < until
               and not all(f._done_callbacks for f in futs if not f.done())):
            time.sleep(0.01)
        futs[4].set_result(4)
        assert futs[3].cancel()
        assert futs[3].set_running_or_notify_cancel() is False   # what a worker does
        futs[2].set_exception(ValueError("two"))
        futs[1].set_result(1)
        futs[0].set_result(0)

    t = threading.Thread(target=finish)
    t.start()
    got = list(common._as_completed(futs + [futs[1]]))
    t.join(30)
    assert got == [futs[4], futs[3], futs[2], futs[1], futs[0]], got
    assert futs[3].cancelled()
    with pytest.raises(ValueError, match="two"):
        futs[2].result()
    assert [f.result() for f in (futs[4], futs[1], futs[0])] == [4, 1, 0]


def test_as_completed_with_a_real_pool():
    """A pool of 16 workers and 3000 quick tasks: every result once."""
    with cf.ThreadPoolExecutor(16) as pool:
        futs = [pool.submit(os.stat, __file__) for _ in range(3000)]
        got = list(common._as_completed(futs))
    assert len(got) == 3000 and set(got) == set(futs)
