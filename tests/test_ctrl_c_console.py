"""C4 (item 1 of plan 261127_Fable_plan_pending-code): a console Ctrl+C no
longer reaches cjxl, djxl, magick or ExifTool. The tools are started in a
process group of their own (_OWN_GROUP), so the first press reaches the script
alone and the files in progress really finish (#702); a press that stops at
once kills them through the script's registry (_LIVE_CHILDREN,
_kill_live_children). The wizard lets the backend finish its files.

Every console test sends a REAL console Ctrl+C. The test starts a helper
script in a console of its own (CREATE_NO_WINDOW: no window, and the event
never reaches pytest or the owner's console); the helper first undoes an
inherited "ignore Ctrl+C" (SetConsoleCtrlHandler(None, 0) — a parent started
with Ctrl+C disabled hands that to every child, and then nothing would die
and no handler would run), then presses with GenerateConsoleCtrlEvent.

Pre-fix proof (each console test but T1.5 fails there; the second-press
tests of a pool and the static pin of M1 fail on the coder's 2.31.1-dev2
worktree too, where the main thread waited in one long wait):
    extract the six scripts of HEAD into a folder, then
    set JXLPHOTO_SCRIPTS_UNDER_TEST=<that folder>
    python -m pytest tests/test_ctrl_c_console.py -q
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)
sys.path.insert(0, str(SCRIPTS))
import jxl_photo as wp  # noqa: E402
import jxl_tiff_encoder as enc  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt", reason="a Windows console's Ctrl+C")

HOSTILE = "250118_吾妻山公園 [FINAL] 100%off %d"

_PRELUDE = r'''
import ctypes, json, os, sys, threading, time, traceback
ctypes.windll.kernel32.SetConsoleCtrlHandler(None, 0)
RESULT, SCRIPTS = sys.argv[1], sys.argv[2]
sys.path.insert(0, SCRIPTS)


def press():
    ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, 0)


def press_in(t):
    threading.Timer(t, press).start()


def done(**kw):
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(kw, f)


def wait_for(results, n, seconds=None):
    """Until `results` holds n items, in small sleeps (the main thread must
    run Python code for a signal handler to run). Never Thread.join: a join
    that a KeyboardInterrupt cuts leaves is_alive() False while the thread
    still runs (CPython up to 3.12). KeyboardInterrupt goes to the caller.
    True when all n came."""
    until = None if seconds is None else time.monotonic() + seconds
    while len(results) < n:
        if until is not None and time.monotonic() > until:
            return False
        time.sleep(0.05)
    return True
'''


def _run_helper(folder: Path, body: str, timeout=120, env=None, args=()):
    """Run `body` (after the prelude) in a console of its own; its JSON."""
    folder.mkdir(parents=True, exist_ok=True)
    helper = folder / "helper.py"
    helper.write_text(_PRELUDE + textwrap.dedent(body), encoding="utf-8")
    result = folder / "result.json"
    out, err = folder / "helper.out", folder / "helper.err"
    with open(out, "wb") as o, open(err, "wb") as e:
        p = subprocess.run([sys.executable, str(helper), str(result), str(SCRIPTS), *args],
                           stdin=subprocess.DEVNULL, stdout=o, stderr=e, timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW,
                           env=dict(os.environ, **(env or {})))
    assert result.exists(), (p.returncode, err.read_bytes().decode("utf-8", "replace")[-3000:])
    return json.loads(result.read_text(encoding="utf-8"))


# --- one press: the tools of the run finish ------------------------------------

_ONE_PRESS = r'''
import jxl_tiff_encoder as enc
enc._reset_abort()
res = {}
finished = []


def work():
    try:
        r = CALL
        out = r.stdout.decode() if isinstance(r.stdout, bytes) else r.stdout
        res.update(rc=r.returncode, out=out)
    except BaseException as e:
        res.update(exc=repr(e))
    finally:
        finished.append(1)


main_exc = None
with enc._ctrl_c_finishes_the_run():
    threading.Thread(target=work).start()
    press_in(0.7)
    try:
        wait_for(finished, 1)
    except BaseException as e:
        main_exc = repr(e)
        wait_for(finished, 1, 30)
done(res=res, interrupted=enc._interrupted(), main_exc=main_exc)
'''


@pytest.mark.parametrize("call", [
    pytest.param('enc._run_captured([sys.executable, "-c", "import time; time.sleep(3); '
                 'print(\'done\')"], 60, text=True)', id="run_captured"),
    pytest.param('enc._run_to_memory([sys.executable, "-c", "import time; time.sleep(3); '
                 'print(\'done\')"], 60)', id="run_to_memory"),
    pytest.param('enc._run_captured([sys.executable, "-c", "import sys,time; '
                 'd=sys.stdin.buffer.read(); time.sleep(3); print(\'done\', len(d))"], 60, '
                 'text=True, input=b"x" * 100000)', id="with_an_input"),
])
def test_a_press_does_not_reach_a_tool(tmp_path, call):
    """T1.1–T1.3: a tool of _run_captured / _run_to_memory / an input run
    finishes after one press, and the run is latched as interrupted."""
    r = _run_helper(tmp_path / HOSTILE, _ONE_PRESS.replace("CALL", call))
    assert r["main_exc"] is None, r
    assert r["res"].get("rc") == 0, r
    assert "done" in r["res"]["out"], r
    if "input" in call:
        assert "100000" in r["res"]["out"], r
    assert r["interrupted"] is True, r


# --- two presses: stop now, the script kills its tools ---------------------------

def test_a_second_press_kills_the_tools_and_stops_at_once(tmp_path):
    """T1.4: a second press kills the tools of worker threads and raises at
    once (before the fix no handler killed them: it fails there)."""
    r = _run_helper(tmp_path / HOSTILE, r'''
    import jxl_tiff_encoder as enc
    enc._reset_abort()
    results = []


    def work():
        try:
            p = enc._run_captured([sys.executable, "-c", "import time; time.sleep(60)"], 120)
            results.append(p.returncode)
        except BaseException as e:
            results.append(repr(e))


    def presser():
        # The second press once the first is latched (S1 of the second
        # review: two presses at fixed times could arrive as ONE call).
        time.sleep(1.0)
        press()
        until = time.monotonic() + 20
        while not enc._interrupted() and time.monotonic() < until:
            time.sleep(0.02)
        time.sleep(0.5)
        press()


    caught, second = None, None
    with enc._ctrl_c_finishes_the_run():
        for _ in range(3):
            threading.Thread(target=work).start()
        threading.Thread(target=presser, daemon=True).start()
        try:
            wait_for(results, 3)
        except KeyboardInterrupt:
            caught = "KeyboardInterrupt"
            second = time.monotonic()
    all_done = wait_for(results, 3, 10)
    done(caught=caught, all_done=all_done,
         took=(time.monotonic() - second) if second else None, results=results,
         live=len(getattr(enc, "_LIVE_CHILDREN", {})))
    ''')
    assert r["caught"] == "KeyboardInterrupt", r
    assert r["all_done"] and r["took"] < 10, r
    assert len(r["results"]) == 3 and all(x != 0 for x in r["results"]), r
    assert r["live"] == 0, r


_POOL = r'''
import concurrent.futures as cf
import jxl_tiff_encoder as enc
from concurrent.futures import ThreadPoolExecutor
# The backends' own wait (M1 of the review): _as_completed; the code before
# it waited with concurrent.futures.as_completed.
as_completed = getattr(enc, "_as_completed", cf.as_completed)
enc._reset_abort()
t0 = time.monotonic()
events = []
latched = []


def work(i):
    if enc._interrupted():
        events.append((round(time.monotonic() - t0, 2), i, "not attempted"))
        return "not attempted"
    events.append((round(time.monotonic() - t0, 2), i, "start"))
    p = enc._run_captured([sys.executable, "-c", "import time; time.sleep(6)"], 120)
    events.append((round(time.monotonic() - t0, 2), i, "rc=%s" % p.returncode))
    return p.returncode


def watch():
    while time.monotonic() - t0 < 30:
        if enc._interrupted():
            latched.append(round(time.monotonic() - t0, 2))
            return
        time.sleep(0.02)


second_at = []


def presser():
    # Each press is sent once the one before was heard (S1 of the second
    # review: on a loaded machine two presses at fixed times could reach the
    # handler as ONE call, and the test then measured the machine).
    time.sleep(1.0)
    press()
    if SECOND:
        until = time.monotonic() + 20
        while not enc._interrupted() and time.monotonic() < until:
            time.sleep(0.02)
        time.sleep(0.5)
        second_at.append(round(time.monotonic() - t0, 2))
        press()


threading.Thread(target=watch, daemon=True).start()
caught, res = None, []
try:
    with enc._ctrl_c_finishes_the_run():
        with ThreadPoolExecutor(2) as ex:
            futs = [ex.submit(work, i) for i in range(4)]
            threading.Thread(target=presser, daemon=True).start()
            for f in as_completed(futs):
                res.append(f.result())
except KeyboardInterrupt:
    caught = round(time.monotonic() - t0, 2)
done(caught_at=caught, total=round(time.monotonic() - t0, 2), res=res,
     events=sorted(events), interrupted=enc._interrupted(),
     latched_at=latched[0] if latched else None,
     second_at=second_at[0] if second_at else None)
'''


def _pool_case(tmp_path, second):
    return _run_helper(tmp_path / HOSTILE, _POOL.replace("SECOND", repr(bool(second))))


def test_a_second_press_stops_a_pool_at_once(tmp_path):
    """M1 (review 261127): two presses while a pool waits for two 6 s tools.
    The main thread waits in steps (_as_completed), so the second press is
    heard at once: both tools are killed, the queued tasks are never
    attempted, the run ends soon after the press. Before (one long
    as_completed wait) both presses arrived as ONE call when a tool ended:
    no KeyboardInterrupt at all, and a queued task started."""
    r = _pool_case(tmp_path, True)
    assert r["second_at"] is not None, r
    assert r["caught_at"] is not None and r["caught_at"] - r["second_at"] < 3.0, r
    assert r["total"] < r["second_at"] + 5, r
    ev = r["events"]
    rcs = [e for e in ev if e[2].startswith("rc=")]
    assert len(rcs) == 2 and all(e[2] != "rc=0" for e in rcs), r
    assert sorted(e[1] for e in ev if e[2] == "not attempted") == [2, 3], r
    assert sorted(e[1] for e in ev if e[2] == "start") == [0, 1], r


def test_the_first_press_is_latched_at_once_and_no_file_starts_after_it(tmp_path):
    """M1: one press while the pool waits: latched within a tick (not when a
    tool ends, 5 s later), the two tools in progress finish (rc 0) and no
    queued task starts after it."""
    r = _pool_case(tmp_path, False)
    assert r["caught_at"] is None, r
    # The tools end at 6 s: a latch before then is a latch at the press (the
    # bound that matters on a loaded machine), not when a tool ended.
    assert r["latched_at"] is not None and r["latched_at"] < 4.5, r
    ev = r["events"]
    assert [e for e in ev if e[2] == "start" and e[0] > r["latched_at"]] == [], r
    assert sorted(e[2] for e in ev if e[2].startswith("rc=")) == ["rc=0", "rc=0"], r
    assert sorted(e[1] for e in ev if e[2] == "not attempted") == [2, 3], r


def test_exiftool_run_by_run_captured_survives_a_press(tmp_path):
    """S4 (review 261127): an ExifTool started by _run_captured (not the
    kept-open one) lives through a press — the hidden console of its own
    (_OWN_CONSOLE). With the group flag alone it died at the press (exit 1,
    measured by the review)."""
    exiftool = shutil.which("exiftool")
    if not exiftool:
        pytest.skip("exiftool not on PATH")
    root = tmp_path / HOSTILE
    root.mkdir(parents=True)
    # ExifTool reads its command line in the ANSI code page: the file it
    # reads sits in a plain-named folder (the helper runs in the hostile one).
    plain = tmp_path / "plain"
    plain.mkdir()
    target = plain / "DSC_0001 (2).txt"
    target.write_text("x", encoding="utf-8")
    call = ('enc._run_captured([%r, "-q", "-if", "sleep 3; 1", "-FileSize", %r], 60, text=True)'
            % (exiftool, str(target)))
    r = _run_helper(root / "helper", _ONE_PRESS.replace("CALL", call))
    assert r["main_exc"] is None, r
    assert r["res"].get("rc") == 0, r
    assert "File Size" in r["res"]["out"], r
    assert r["interrupted"] is True, r


def test_the_main_thread_never_waits_for_a_pool_in_one_long_wait():
    """M1, static: the four backends wait for their pools only through
    _as_completed / _future_result (waits of _WAIT_TICK), never with
    concurrent.futures.as_completed or a pool's map."""
    import ast
    for name in ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
                 "jxl_jpeg_transcoder.py", "jxl_recompressor.py"):
        tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "as_completed":
                bad.append(("as_completed", node.lineno))
            elif isinstance(node, ast.alias) and node.name == "as_completed":
                bad.append(("import as_completed", 0))
            elif (isinstance(node, ast.Attribute) and node.attr in ("as_completed", "map")
                  and isinstance(node.ctx, ast.Load)):
                bad.append((node.attr, node.lineno))
        assert bad == [], (name, bad)
    assert "_check.result(" not in (SCRIPTS / "jxl_tiff_encoder.py").read_text(encoding="utf-8")
    common = (SCRIPTS / "jxl_photo_common.py").read_text(encoding="utf-8")
    assert "_WAIT_TICK" in common and "def _as_completed(" in common


# The conversion pools: waited for inside _ctrl_c_finishes_the_run, where a
# press latches the abort and the queued tasks return "not attempted".
_ARMED_POOLS = {("jxl_tiff_encoder.py", "process_group"),
                ("jxl_tiff_decoder.py", "process_group"),
                ("jxl_jpeg_transcoder.py", "process_group_transcode"),
                ("jxl_jpeg_transcoder.py", "process_group_convert"),
                ("jxl_recompressor.py", "process_group")}


def test_every_other_pool_cancels_its_queue_when_its_loop_is_left():
    """M2 (second review 261127), static: a `for ... in _as_completed(...)`
    outside the conversion pools (a planning, --repair-jbrd,
    --refresh-explorer-thumbnails) sits in a try whose handler calls
    shutdown(..., cancel_futures=True): what a pool's map did when it was
    left, and what _as_completed does not do. Without it a Ctrl+C there
    left every queued file to run, and no further press was heard. A call of
    `_as_completed` anywhere else (a comprehension, `list(...)`) is refused:
    this check could not see it."""
    import ast
    uncovered = []
    for name in ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
                 "jxl_jpeg_transcoder.py", "jxl_recompressor.py"):
        tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        loop_iters = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Call):
                it = node.iter
                loop_iters.add(id(it))
                if (isinstance(it.func, ast.Name) and it.func.id == "enumerate" and it.args
                        and isinstance(it.args[0], ast.Call)):
                    loop_iters.add(id(it.args[0]))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_as_completed" and id(node) not in loop_iters):
                uncovered.append((name, "not the iterator of a for statement", node.lineno))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.For) and isinstance(node.iter, ast.Call)):
                continue
            it = node.iter
            if (isinstance(it.func, ast.Name) and it.func.id == "enumerate" and it.args
                    and isinstance(it.args[0], ast.Call)):
                it = it.args[0]
            if not (isinstance(it.func, ast.Name) and it.func.id == "_as_completed"):
                continue
            covered, func, p = False, None, node
            while p in parents:
                p = parents[p]
                if isinstance(p, ast.Try):
                    covered = covered or any(
                        isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "shutdown"
                        and any(k.arg == "cancel_futures" and getattr(k.value, "value", None)
                                is True for k in n.keywords)
                        for h in p.handlers for n in ast.walk(h))
                if func is None and isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func = p.name
            if not covered and (name, func) not in _ARMED_POOLS:
                uncovered.append((name, func, node.lineno))
    assert uncovered == [], uncovered


def test_a_press_stops_the_thumbnail_refresh_and_starts_no_queued_file(tmp_path):
    """M2 (second review 261127): --refresh-explorer-thumbnails over 16 files,
    2 workers, each file a 1 s tool; one press at 1.5 s. The handler each
    backend's __main__ installs raises at once: the tools in progress are
    killed, the queued files never start, the mode ends within seconds.
    Before the fix the pool's exit waited for all 16 (8.4 s)."""
    r = _run_helper(tmp_path / HOSTILE, r'''
    from pathlib import Path
    import jxl_recompressor as rc
    stops = getattr(rc._common, "_ctrl_c_stops_children", None)
    if stops is not None:
        stops(rc._G)
    t0 = time.monotonic()
    started_at, pressed = [], []


    def one(f, dry_run):
        started_at.append(round(time.monotonic() - t0, 2))
        rc._run_captured([sys.executable, "-c", "import time; time.sleep(1)"], 60)
        return "current", ""


    rc._refresh_one_thumbnail = one
    files = [Path(RESULT).parent / f"DSC_{i:04d} (2) [b].jxl" for i in range(16)]


    def press_once():
        pressed.append(round(time.monotonic() - t0, 2))
        press()


    threading.Timer(1.5, press_once).start()
    caught = None
    try:
        rc._refresh_explorer_thumbnails(files, 2, True)
    except KeyboardInterrupt:
        caught = round(time.monotonic() - t0, 2)
    total = round(time.monotonic() - t0, 2)
    time.sleep(1.5)        # a queued file that still started would show here
    done(caught_at=caught, total=total, n_started=len(started_at),
         started_after_press=len([s for s in started_at if pressed and s > pressed[0] + 0.3]))
    ''', env={"JXLPHOTO_JXL_THUMB": "0"})
    assert r["caught_at"] is not None, r
    assert r["n_started"] <= 8, r
    assert r["total"] < 6, r


def test_a_press_outside_a_run_kills_the_worker_threads_tools(tmp_path):
    """T1.5: the handler each backend's __main__ installs (planning, a
    maintenance mode) kills a worker thread's tool and raises at once. Before
    the fix (no such handler: Python's own) it passes by the same accident."""
    r = _run_helper(tmp_path / HOSTILE, r'''
    import jxl_tiff_encoder as enc
    stops = getattr(enc._common, "_ctrl_c_stops_children", None)
    if stops is not None:
        stops(enc._G)
    results = []


    def work():
        try:
            p = enc._run_captured([sys.executable, "-c", "import time; time.sleep(60)"], 120)
            results.append(p.returncode)
        except BaseException as e:
            results.append(repr(e))


    threading.Thread(target=work).start()
    time.sleep(0.3)
    press_in(0.7)
    caught, at = None, None
    try:
        wait_for(results, 1)
    except KeyboardInterrupt:
        caught, at = "KeyboardInterrupt", time.monotonic()
    all_done = wait_for(results, 1, 10)
    done(caught=caught, all_done=all_done, took=(time.monotonic() - at) if at else None,
         results=results)
    ''')
    assert r["caught"] == "KeyboardInterrupt", r
    assert r["all_done"] and r["took"] < 10, r
    assert r["results"] and r["results"][0] != 0, r


def test_the_kept_open_exiftool_survives_a_press_and_dies_at_the_second(tmp_path):
    """T1.6. The first half (survives a press) passes before the fix too
    (P-C4: ExifTool keeps running either way); the second half needs
    _kill_live_children."""
    if not (shutil.which("exiftool") or shutil.which("exiftool(-k)")):
        pytest.skip("exiftool not on PATH")
    r = _run_helper(tmp_path / HOSTILE, r'''
    import jxl_tiff_encoder as enc
    enc._reset_abort()
    pool = enc._EXIFTOOL_POOL
    first = enc._run_exiftool_argfile(["-ver"], timeout=60)
    started1 = pool.started
    with enc._ctrl_c_finishes_the_run():
        press()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not enc._interrupted():
            time.sleep(0.05)
        second = enc._run_exiftool_argfile(["-ver"], timeout=60)
    kill = getattr(enc._common, "_kill_live_children", None)
    killed = False
    if kill is not None:
        kill(enc._G)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(s.proc is None or s.proc.poll() is not None for s in list(pool.live)):
                killed = True
                break
            time.sleep(0.05)
    done(rc1=first.returncode, started1=started1, rc2=second.returncode,
         started2=pool.started, interrupted=enc._interrupted(), killed=killed,
         has_kill=kill is not None)
    ''')
    assert r["rc1"] == 0 and r["started1"] == 1, r
    assert r["interrupted"] is True, r
    assert r["rc2"] == 0 and r["started2"] == 1, r
    assert r["has_kill"] and r["killed"], r


# --- a real encode ----------------------------------------------------------------

def test_a_real_encode_in_progress_finishes_after_one_press(tmp_path):
    """T1.7: three lossless effort-9 encodes, one worker; a press while cjxl
    reads the first PNG. Every file in progress finishes and is a valid JXL
    (with one worker the encoder already prepares the next file — its TIFF
    read, its ExifTool reads — while cjxl runs, so two can be in progress);
    no file is an error; the others are 'never attempted'; the run ends as
    an interrupt."""
    for tool in ("cjxl", "djxl", "exiftool"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} not on PATH")
    np = pytest.importorskip("numpy")
    tifffile = pytest.importorskip("tifffile")
    root = tmp_path / HOSTILE
    src, out, logs = root / "in", root / "out", root / "logs"
    src.mkdir(parents=True)
    rng = np.random.default_rng(702)
    for name in ("DSC_0001 (2).tif", "DSC_0002 [b].tif", "吾妻山 100%off %d.tif"):
        tifffile.imwrite(src / name, rng.integers(0, 65535, (2000, 3000, 3), dtype=np.uint16),
                         photometric="rgb")
    r = _run_helper(root / "helper", r'''
    import jxl_tiff_encoder as enc
    real = enc._common._write_stdin
    fired = []


    def watching(p, data):
        if not fired and len(data) > (1 << 20):
            fired.append(1)
            threading.Timer(0.3, press).start()
        return real(p, data)


    enc._common._write_stdin = watching
    sys.argv = ["jxl_tiff_encoder.py", sys.argv[3], sys.argv[4], "--mode", "0",
                "--workers", "1", "--effort", "9", "--distance", "0"]
    caught = None
    try:
        enc.main()
    except BaseException as e:
        caught = type(e).__name__
    done(caught=caught, fired=bool(fired))
    ''', timeout=600, env={"JXLPHOTO_LOG_DIR": str(logs)}, args=(str(src), str(out)))
    assert r["fired"], r
    assert r["caught"] == "KeyboardInterrupt", r
    log = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                    for p in logs.rglob("*.log"))
    assert "Interrupted (Ctrl+C)" in log
    import re
    done_line = re.search(r"Done: (\d+) OK \| \d+ overwrites \| \d+ skipped \| (\d+) errors", log)
    never = re.search(r"(\d+) file\(s\) were never attempted", log)
    assert done_line and never, log[-3000:]
    ok, errors, not_attempted = int(done_line[1]), int(done_line[2]), int(never[1])
    assert errors == 0, log[-3000:]
    assert ok >= 1 and not_attempted >= 1 and ok + not_attempted == 3, log[-3000:]
    jxls = sorted(out.rglob("*.jxl"))
    assert len(jxls) == ok, jxls
    for i, j in enumerate(jxls):
        assert enc._verify_jxl_integrity(j)
        d = subprocess.run(["djxl", str(j), str(root / f"check{i}.ppm")],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
        assert d.returncode == 0, d.stderr[-500:]
    assert not list(out.rglob("*.tmp"))


def test_a_real_encode_stops_at_the_second_press(tmp_path):
    """M1 (review 261127), T1.7 with two presses (0.3 s after cjxl got its
    PNG, then 0.5 s after the first is latched): the second press kills the
    tools and the run ends soon after — before the fix the main thread heard
    it only when the encode ended. No partial JXL, no temp."""
    for tool in ("cjxl", "djxl", "exiftool"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} not on PATH")
    np = pytest.importorskip("numpy")
    tifffile = pytest.importorskip("tifffile")
    root = tmp_path / HOSTILE
    src, out, logs = root / "in", root / "out", root / "logs"
    src.mkdir(parents=True)
    rng = np.random.default_rng(702)
    for name in ("DSC_0001 (2).tif", "DSC_0002 [b].tif", "吾妻山 100%off %d.tif"):
        tifffile.imwrite(src / name, rng.integers(0, 65535, (2000, 3000, 3), dtype=np.uint16),
                         photometric="rgb")
    r = _run_helper(root / "helper", r'''
    import jxl_tiff_encoder as enc
    real = enc._common._write_stdin
    fired = []
    second = []


    def press_second():
        # Sent once the first press is latched (S1 of the second review).
        until = time.monotonic() + 60
        while not enc._interrupted() and time.monotonic() < until:
            time.sleep(0.02)
        time.sleep(0.5)
        second.append(time.monotonic())
        press()


    def watching(p, data):
        if not fired and len(data) > (1 << 20):
            fired.append(time.monotonic())
            threading.Timer(0.3, press).start()
            threading.Thread(target=press_second, daemon=True).start()
        return real(p, data)


    enc._common._write_stdin = watching
    sys.argv = ["jxl_tiff_encoder.py", sys.argv[3], sys.argv[4], "--mode", "0",
                "--workers", "1", "--effort", "9", "--distance", "0"]
    caught = None
    try:
        enc.main()
    except BaseException as e:
        caught = type(e).__name__
    end = time.monotonic()
    done(caught=caught, fired=bool(fired),
         after_second=(end - second[0]) if second else None)
    ''', timeout=600, env={"JXLPHOTO_LOG_DIR": str(logs)}, args=(str(src), str(out)))
    assert r["fired"], r
    assert r["caught"] == "KeyboardInterrupt", r
    # Before the fix: the whole encode after the press (20 s on an idle machine).
    assert r["after_second"] is not None and r["after_second"] < 30, r
    # The file in progress is killed — or, on a loaded machine, finished
    # before the second press: never a partial JXL, never a temp.
    jxls = list(out.rglob("*.jxl"))
    assert len(jxls) <= 1 and all(enc._verify_jxl_integrity(j) for j in jxls), jxls
    assert not list(out.rglob("*.tmp"))
    log = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                    for p in logs.rglob("*.log"))
    assert "Interrupted (Ctrl+C)" in log
    assert "ERROR" in log.replace("Interrupted (Ctrl+C)", ""), log[-3000:]


# --- the wizard ---------------------------------------------------------------------

_WIZARD = r'''
from pathlib import Path
import jxl_photo as wp
wp.RICH_AVAILABLE = False
wp.console = None
cfgpath = Path(RESULT).parent / ".jxl_tools_config.json"
wp.ConfigManager._get_config_path = lambda self: cfgpath
cfg = wp.ConfigManager()
menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
lines = []
menu._print_child_line = lambda line: lines.append(line.strip())
notes = []
menu._ui = wp.PlainUI()            # menu.ui hands out a new one at each call
_markup = menu._ui.markup


def _noting(rich, plain=None):
    if isinstance(plain, str) and plain.startswith("Ctrl+C"):
        notes.append(plain)
    return _markup(rich, plain)


menu._ui.markup = _noting
backend, started, marker, pidfile = sys.argv[3:7]


def presser():
    # The first press 1 s after the backend started; each next one 0.5 s
    # after the wizard printed its note for the one before (S1 of the second
    # review: presses at fixed times could reach the wizard as ONE).
    while not os.path.exists(started):
        time.sleep(0.02)
    time.sleep(1.0)
    for k in range(PRESSES):
        if k:
            until = time.monotonic() + 20
            while len(notes) < k and time.monotonic() < until:
                time.sleep(0.02)
            time.sleep(0.5)
        press()


threading.Thread(target=presser, daemon=True).start()
t0 = time.monotonic()
caught, rc = None, None
try:
    rc = menu._stream_child([sys.executable, backend, started, marker, pidfile])
except KeyboardInterrupt:
    caught = "KeyboardInterrupt"
done(caught=caught, rc=rc, took=time.monotonic() - t0, lines=lines, notes=notes,
     last_rc=getattr(menu, "_last_child_rc", "missing"), marker=os.path.exists(marker))
'''

_FAKE_BACKEND_FINISHES = r'''
import ctypes, os, signal, sys, time
ctypes.windll.kernel32.SetConsoleCtrlHandler(None, 0)
started, marker, pidfile = sys.argv[1:4]
flag = []
signal.signal(signal.SIGINT, lambda s, f: flag.append(1))
open(pidfile, "w").write(str(os.getpid()))
print("started", flush=True)
open(started, "w").write("x")
while not flag:
    time.sleep(0.05)
time.sleep(2)
print("summary", flush=True)
open(marker, "w").write("x")
sys.exit(130)
'''

_FAKE_BACKEND_DEAF = r'''
import ctypes, os, signal, sys, time
ctypes.windll.kernel32.SetConsoleCtrlHandler(None, 0)
started, marker, pidfile = sys.argv[1:4]
signal.signal(signal.SIGINT, signal.SIG_IGN)
open(pidfile, "w").write(str(os.getpid()))
print("started", flush=True)
open(started, "w").write("x")
time.sleep(60)
open(marker, "w").write("x")
'''


def _wizard_case(tmp_path, backend_src, presses):
    root = tmp_path / HOSTILE
    root.mkdir(parents=True)
    backend = root / "fake backend.py"
    backend.write_text(backend_src, encoding="utf-8")
    files = [str(root / n) for n in ("started", "marker", "pid")]
    r = _run_helper(root / "helper", _WIZARD.replace("PRESSES", repr(int(presses))),
                    args=(str(backend), *files))
    return r, Path(files[2])


def test_the_wizard_lets_the_backend_finish(tmp_path):
    """T1.8: one press — the backend ends by itself (its summary printed),
    then the wizard ends as after any Ctrl+C."""
    r, _pid = _wizard_case(tmp_path, _FAKE_BACKEND_FINISHES, 1)
    assert r["caught"] == "KeyboardInterrupt", r
    assert r["marker"] is True, r
    assert "summary" in r["lines"], r
    assert r["last_rc"] == 130, r


def _pid_alive(pid: int) -> bool:
    import ctypes
    h = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        return code.value == 259            # STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def test_the_wizard_kills_at_the_third_press(tmp_path):
    """T1.9: a backend that does not stop is killed at the third press."""
    r, pidfile = _wizard_case(tmp_path, _FAKE_BACKEND_DEAF, 3)
    assert r["caught"] == "KeyboardInterrupt", r
    assert len(r["notes"]) >= 2, r
    assert r["took"] < 30, r
    assert r["marker"] is False, r
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _pid_alive(pid)


# --- no console ---------------------------------------------------------------------

def test_run_captured_starts_its_child_in_a_group_of_its_own(monkeypatch):
    """T1.10: the real path passes CREATE_NEW_PROCESS_GROUP; a test's
    stand-in for subprocess.run is called exactly as before."""
    seen = []
    real_popen = subprocess.Popen

    class _Recording(real_popen):
        def __init__(self, *a, **k):
            seen.append(k.get("creationflags", 0))
            super().__init__(*a, **k)

    monkeypatch.setattr(subprocess, "Popen", _Recording)
    r = enc._run_captured([sys.executable, "-c", "print(1)"], 60, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "1"
    assert seen == [subprocess.CREATE_NEW_PROCESS_GROUP], seen
    monkeypatch.undo()

    calls = []

    def fake_run(cmd, **kw):
        calls.append(sorted(kw))
        return subprocess.CompletedProcess(cmd, 0, None, None)

    monkeypatch.setattr(enc.subprocess, "run", fake_run)
    enc._run_captured([sys.executable, "-c", "print(1)"], 60)
    assert calls == [["stderr", "stdin", "stdout", "timeout"]], calls


def test_the_wizards_taskkill_runs_in_a_group_of_its_own(monkeypatch):
    """S3 (review 261127): the wizard's taskkill does not share the console's
    Ctrl+C — a press while it runs must not kill it before it took the tree."""
    real_run = subprocess.run
    seen = []

    def recording(cmd, **kw):
        if cmd and cmd[0] == "taskkill":
            seen.append(kw.get("creationflags", 0))
        return real_run(cmd, **kw)

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             stdin=subprocess.DEVNULL)
    try:
        monkeypatch.setattr(wp.subprocess, "run", recording)
        wp._kill_process_tree(child)
        child.wait(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
    assert len(seen) == 1 and seen[0] & subprocess.CREATE_NEW_PROCESS_GROUP, seen


def test_exiftool_also_gets_a_hidden_console_of_its_own():
    """ExifTool (Perl) dies of a press in a group of its own (measured):
    every ExifTool start also gets CREATE_NO_WINDOW; the codecs do not."""
    common = enc._common
    grp, nowin = subprocess.CREATE_NEW_PROCESS_GROUP, subprocess.CREATE_NO_WINDOW
    for name in (r"C:\x y\exiftool.exe", "exiftool", r"C:\t\exiftool(-k).exe", "exiftool-k"):
        assert common._own_group_for([name, "-ver"]) == {"creationflags": grp | nowin}, name
    for name in (r"C:\l\cjxl.exe", "djxl", "magick", sys.executable):
        assert common._own_group_for([name]) == {"creationflags": grp}, name


def _manifest_interrupted(monkeypatch, tmp_path, rc_done):
    monkeypatch.setattr(wp.ConfigManager, "_get_config_path",
                        lambda self: tmp_path / ".jxl_tools_config.json")
    cfg = wp.ConfigManager()
    menu = wp.InteractiveMenu(cfg, wp.DependencyChecker(cfg))
    rendered = []

    def interrupted_run(self, cmd):
        self._last_child_rc = rc_done
        self._last_child_summary = None
        raise KeyboardInterrupt

    monkeypatch.setattr(wp.InteractiveMenu, "_run_subprocess", interrupted_run)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_lossy_delete_skipped",
                        lambda self, wf: None)
    monkeypatch.setattr(wp.InteractiveMenu, "_confirm_archive_mode", lambda self: True)
    monkeypatch.setattr(wp.InteractiveMenu, "_render_manifest_summary",
                        lambda self, reports, ok, skip, err: rendered.append(
                            (list(reports), ok, skip, err)))
    monkeypatch.setattr(wp.FolderAnalyzer, "detect_mode_for_entry",
                        lambda self, s, d, original_mode=0: original_mode)
    monkeypatch.setattr(wp, "RICH_AVAILABLE", False)
    monkeypatch.setattr(wp, "console", None)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    root = tmp_path / HOSTILE
    entries = []
    for i in range(3):
        src = root / f"src {i}"
        src.mkdir(parents=True)
        (src / f"DSC_000{i} (2).jxl").write_bytes(b"x")
        entries.append((str(src), str(root / f"out {i}"), 2))
    wf = {
        "origin_format": "jxl", "dest_format": "png", "mode": 99,
        "workers": 1, "quality": 95, "bit_depth": 16, "staging": None,
        "dry_run": False, "mode_config": {},
        "conversion_type": "jxl_to_png", "icc_profile": None, "distance": 1.0,
        "effort": 7, "advanced_options": {},
        "manifest_entries": entries, "unattended": False,
    }
    with pytest.raises(KeyboardInterrupt):
        menu._execute_manifest_workflow(wf, {})
    assert len(rendered) == 1
    return rendered[0]


def test_the_manifest_records_a_row_that_ended_after_a_press(monkeypatch, tmp_path):
    """T1.11: the row the press stopped is 'interrupted' (exit 130 after its
    summary) or 'ok' (exit 0: it was done), the others 'not started'."""
    reports, ok, skip, err = _manifest_interrupted(monkeypatch, tmp_path, 130)
    assert [r["state"] for r in reports] == ["interrupted", "not started", "not started"]
    assert (ok, err) == (0, 1)
    reports, ok, skip, err = _manifest_interrupted(monkeypatch, tmp_path / "b", 0)
    assert [r["state"] for r in reports] == ["ok", "not started", "not started"]
    assert (ok, err) == (1, 0)
