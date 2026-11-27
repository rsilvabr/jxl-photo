"""#769 (third review 261128, M1 + S1): a writer of checksums.md5 killed while
it held the folder's lock (`<db>.lock`).

Before: the lock was the file's existence alone. A killed writer left it, and
for 120 s (_MD5_LOCK_STALE_S) every writer of that folder waited 30 s
(_MD5_LOCK_TIMEOUT_S) and dropped its line — a deleting run kept every source
("KEEP (MD5 not confirmed)"). Now, on Windows, the holder keeps the lock file
open with one byte of it locked and a marker in it: a waiter that finds the
marker and gets the byte knows the writer is gone and takes the lock over in
place.

S1: a writer killed inside its append leaves half a line; the next line now
starts on a line of its own instead of being glued to it.

The lock fix's review (261128):
- S1: a creator that cannot take its own lock byte for 2 s because another
  writer took the lock over meanwhile (a share's clock behind, the PC
  asleep) must not append without the lock: fail closed.
- S2 (the owner's yes): a writer killed between its create and its marker
  leaves an EMPTY lock — measured, the lock is empty for 12-31 % of its
  life, so roughly one kill in four to eight during a hold. Such a lock,
  its byte free, is taken over in place once it is older than 10 s
  (_MD5_LOCK_EMPTY_S), under the waiters' 30 s: no line dropped. The 120 s
  rule stays only for an empty lock whose byte is held or cannot be tested.

Every test runs a HELPER process (the real function, killed with taskkill /F
where it is the point), in a folder with Japanese, brackets, spaces and
`100%off %d`.

Pre-fix proof (tests 1, 3, 5, 6, 7 fail there; 2 and 4 are guard tests; the
creator that lost its byte fails on the code before the review's S1 fix, the
empty lock taken over at 10 s before its S2 fix):
    copy the six scripts of the pre-fix code into a folder, then
    $env:JXLPHOTO_SCRIPTS_UNDER_TEST = "<that folder>"
    python -m pytest tests/test_md5_lock_killed_writer.py -q -p no:cacheprovider
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = Path(os.environ.get("JXLPHOTO_SCRIPTS_UNDER_TEST") or REPO)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the Windows byte lock")

_HELPER = r'''
import builtins, json, logging, sys, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import jxl_jpeg_transcoder as tr

role = sys.argv[2]
warned = []


class _Collect(logging.Handler):
    def emit(self, r):
        if r.levelno >= logging.WARNING:
            warned.append(r.getMessage())


tr.logger.addHandler(_Collect())
if tr.logger.level == logging.NOTSET or tr.logger.level > logging.WARNING:
    tr.logger.setLevel(logging.WARNING)

if role == "hold":
    db, tag, secs = Path(sys.argv[3]), sys.argv[4], float(sys.argv[5])

    class _Held:
        def __enter__(self):
            Path(str(db) + f".{tag}.ready").write_text("1")
            time.sleep(secs)

        def __exit__(self, *a):
            return False

    tr._md5_db_lock = _Held()
    ok = tr._append_checksum_line(db, f"{tag}  held.jxl\n")
    Path(str(db) + f".{tag}.done").write_text(json.dumps({"ok": ok, "warned": warned}))

elif role == "append":
    db, timeout, text = Path(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
    tr._MD5_LOCK_TIMEOUT_S = timeout
    t0 = time.monotonic()
    ok = tr._append_checksum_line(db, text + "\n")
    took = time.monotonic() - t0
    read = None
    if len(sys.argv) > 6:
        read = tr.read_md5_db(db.parent / sys.argv[6])
    print(json.dumps({"ok": ok, "seconds": took, "warned": warned, "read": read}))

elif role == "write":
    db, tag = Path(sys.argv[3]), sys.argv[4]
    n_threads, n, timeout = int(sys.argv[5]), int(sys.argv[6]), float(sys.argv[7])
    tr._MD5_LOCK_TIMEOUT_S = timeout

    class _SlowFile:
        """An append in two halves with a pause: two writers inside the lock
        at once tear each other's lines."""
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def write(self, s):
            h = len(s) // 2
            self.f.write(s[:h]); self.f.flush()
            time.sleep(0.002)
            self.f.write(s[h:]); self.f.flush()

    def slow_open(path, mode="r", *a, **k):
        f = builtins.open(path, mode, *a, **k)
        return _SlowFile(f) if str(path).endswith(tr.CHECKSUMS_FILENAME) and "a" in mode else f

    tr.open = slow_open
    lock = threading.Lock()
    log = builtins.open(str(db) + f".{tag}.log", "a", encoding="utf-8")

    def work(t):
        for j in range(n):
            name = f"{tag}-{t}-{j:04d}"
            ok = tr._append_checksum_line(db, f"{'a' * 32}  {name} 菜 [x].jxl\n")
            with lock:
                log.write(f"{name} {ok}\n"); log.flush()

    ts = [threading.Thread(target=work, args=(t,)) for t in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    log.close()

elif role == "stolen":
    # The creator's O_EXCL create returns, and before it can lock its byte
    # another writer (here a second handle) takes the lock over: it locks
    # the byte and writes its marker, and keeps both for the whole run.
    import msvcrt, os
    db = Path(sys.argv[3])
    other = tr._MD5_LOCK_MAGIC + b"999.other\n"
    real_open = os.open
    kept = []

    def stealing_open(path, flags, *a, **k):
        fd = real_open(path, flags, *a, **k)
        if flags & os.O_EXCL and str(path).endswith(".lock") and not kept:
            o = real_open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
            os.write(o, other)
            os.lseek(o, tr._MD5_LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(o, msvcrt.LK_NBLCK, 1)
            kept.append(o)
        return fd

    tr._MD5_LOCK_RELEASE_S = 0.5
    tr.os.open = stealing_open
    ok = tr._append_checksum_line(db, "4321  stolen.jxl\n")
    tr.os.open = real_open
    lock = db.parent / (db.name + ".lock")
    print(json.dumps({"ok": ok, "warned": warned, "db": db.exists(),
                      "lock": lock.read_bytes().decode("ascii") if lock.exists() else None}))

elif role == "release":
    lock, in_file, mine = Path(sys.argv[3]), sys.argv[4], sys.argv[5]
    lock.write_bytes(tr._MD5_LOCK_MAGIC + in_file.encode("ascii") + b"\n")
    tr._release_checksum_lock(lock, tr._MD5_LOCK_MAGIC + mine.encode("ascii") + b"\n")
    print(json.dumps({"exists": lock.exists()}))
'''


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "吾妻山 [lock] 100%off %d" / "出力 (2)"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def helper(tmp_path):
    p = tmp_path / "lock_helper.py"
    p.write_text(_HELPER, encoding="utf-8")
    return p


def _cmd(helper, *args):
    return [sys.executable, str(helper), str(SCRIPTS), *map(str, args)]


def _spawn(helper, *args):
    return subprocess.Popen(_cmd(helper, *args), stdin=subprocess.DEVNULL,
                            env=os.environ.copy())


def _run(helper, *args, timeout=120):
    r = subprocess.run(_cmd(helper, *args), stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       env=os.environ.copy())
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _kill(p):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    p.wait()


def _wait_for(path: Path, seconds=60):
    until = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < until, f"{path} never appeared"
        time.sleep(0.01)


def _lock_of(db: Path) -> Path:
    return db.parent / (db.name + ".lock")


def test_a_killed_writers_lock_is_taken_over(folder, helper):
    db = folder / "checksums.md5"
    holder = _spawn(helper, "hold", db, "K", 600)
    try:
        _wait_for(Path(str(db) + ".K.ready"))
        assert _lock_of(db).exists()
    finally:
        _kill(holder)
    res = _run(helper, "append", db, 8, "1234  after the kill.jxl")
    assert res["ok"] is True, res
    assert res["seconds"] < 5, res
    assert res["warned"] == []
    assert db.read_text(encoding="utf-8") == "1234  after the kill.jxl\n"
    assert not _lock_of(db).exists()


def test_a_live_writers_lock_is_never_taken(folder, helper):
    """Guard test (passes on the pre-fix code too): a holder alive for 4 s is
    waited for, never taken over."""
    db = folder / "checksums.md5"
    holder = _spawn(helper, "hold", db, "L", 4)
    try:
        _wait_for(Path(str(db) + ".L.ready"))
        res = _run(helper, "append", db, 30, "5678  waiter.jxl")
        holder.wait(timeout=60)
    finally:
        if holder.poll() is None:
            _kill(holder)
    assert res["ok"] is True, res
    assert res["seconds"] > 3, res
    done = json.loads(Path(str(db) + ".L.done").read_text())
    assert done == {"ok": True, "warned": []}
    assert db.read_text(encoding="utf-8").splitlines() == ["L  held.jxl", "5678  waiter.jxl"]
    assert not _lock_of(db).exists()


def _empty_lock_watcher(lock: Path, seen: threading.Event, stop: threading.Event):
    """Sets `seen` when the lock is EMPTY and more than 15 s old. A writer
    killed between creating the lock and writing its marker leaves an empty
    lock (roughly one kill in four to eight during a hold, review 261128 S2;
    this test's slowed append makes it rarer), which a waiter takes over at
    10 s (_MD5_LOCK_EMPTY_S). One still there at 15 s means that take-over
    did not happen (the pre-fix code keeps it 120 s)."""
    while not stop.is_set():
        try:
            if time.time() - lock.stat().st_mtime > 15 and lock.read_bytes() == b"":
                seen.set()
                return
        except OSError:
            pass
        time.sleep(0.1)


def _stress_attempt(folder: Path, helper):
    """One run of the scenario below: (db, the lock went empty and stuck)."""
    folder.mkdir(parents=True)
    db = folder / "checksums.md5"
    seen, stop = threading.Event(), threading.Event()
    watcher = threading.Thread(target=_empty_lock_watcher, args=(_lock_of(db), seen, stop),
                               daemon=True)
    watcher.start()
    survivors = [_spawn(helper, "write", db, f"P{i}", 4, 60, 20) for i in range(3)]
    try:
        for i in range(2):
            v = _spawn(helper, "write", db, f"V{i}", 4, 100000, 20)
            try:
                # Kill it while it writes: after its first append, not during
                # its start-up (an import can take longer than 0.7 s).
                vlog = Path(str(db) + f".V{i}.log")
                until = time.monotonic() + 30
                while not (vlog.exists() and vlog.stat().st_size) and time.monotonic() < until:
                    time.sleep(0.01)
                time.sleep(0.7)
            finally:
                _kill(v)
        until = time.monotonic() + 300
        while any(p.poll() is None for p in survivors) and time.monotonic() < until:
            if seen.is_set():
                break                   # an empty lock stuck past its 10 s
            time.sleep(0.05)
    finally:
        stop.set()
        for p in survivors:
            if p.poll() is None:
                _kill(p)
    # Every writer is gone now. An empty lock still there (the second kill
    # came after the survivors were done, before the marker): let it reach
    # the 10 s take-over age, so the next writer must take it at once.
    try:
        until = time.monotonic() + 20
        while (_lock_of(db).read_bytes() == b""
               and time.time() - _lock_of(db).stat().st_mtime <= 11
               and time.monotonic() < until):
            time.sleep(0.1)
    except OSError:
        pass
    return db, seen.is_set()


def test_writers_survive_killed_writers_and_tear_no_line(folder, helper):
    """3 writer processes (4 threads each) and, while they run, two more
    killed with taskkill /F inside their appends (slowed and split in two:
    two writers inside at once would tear each other's lines). Every line a
    survivor said it wrote is there once and whole, none was refused, and
    the only half lines are a killed writer's own.

    A kill between the lock's creation and its marker leaves an EMPTY lock,
    taken over at 10 s (review 261128, S2) — under the waiters' 30 s, so no
    line is refused then either. A watcher reports an empty lock still there
    at 15 s; the attempt is then run again (at most 3 attempts; the last one
    is judged whatever happens). On the pre-#769 code every lock is empty
    and stays 120 s, so every attempt sees it and the last one fails."""
    for attempt in range(3):
        db, empty_stuck = _stress_attempt(folder / f"try {attempt}", helper)
        if not empty_stuck:
            break
    assert not empty_stuck, "a killed writer's lock stayed (empty) in 3 attempts"
    tail = " 菜 [x].jxl"
    lines = db.read_text(encoding="utf-8").splitlines()
    whole = [ln for ln in lines if ln.startswith("a" * 32 + "  ") and ln.endswith(tail)]
    names = [ln[34:-len(tail)] for ln in whole]
    said = {}
    for i in range(3):
        for ln in Path(str(db) + f".P{i}.log").read_text(encoding="utf-8").splitlines():
            name, ok = ln.split()
            said[name] = ok == "True"
    assert len(said) == 3 * 4 * 60
    refused = sorted(n for n, ok in said.items() if not ok)
    assert refused == [], refused[:5]
    for n in said:
        assert names.count(n) == 1, n
    torn = [ln for ln in lines if ln not in whole]
    for ln in torn:
        assert ln.startswith("a") and "P" not in ln, ln
    # The second kill may land inside a killed writer's append AFTER the
    # survivors are done: nobody came after it, and its lock is still there.
    # The next writer must get it at once and leave none.
    res = _run(helper, "append", db, 8, "9999  after the writers.jxl")
    assert res["ok"] is True and res["seconds"] < 5 and res["warned"] == [], res
    assert not _lock_of(db).exists()
    print(f"\n#769 stress: {len(lines)} lines, {len(torn)} half line(s) of a killed writer")


def test_an_older_versions_lock_keeps_its_rule(folder, helper):
    """Guard test (passes on the pre-fix code too): a FRESH empty lock may be
    a live writer's (an older version's, or this version's just before it
    locks its byte). Waited for, then fail closed; reclaimed when it is
    older than _MD5_LOCK_STALE_S."""
    db = folder / "checksums.md5"
    lock = _lock_of(db)
    lock.write_bytes(b"")
    res = _run(helper, "append", db, 2, "1  fresh.jxl")
    assert res["ok"] is False, res
    assert not db.exists()
    old = time.time() - 130
    os.utime(lock, (old, old))
    res = _run(helper, "append", db, 5, "2  stale.jxl")
    assert res["ok"] is True, res
    assert db.read_text(encoding="utf-8") == "2  stale.jxl\n"
    assert not lock.exists()


def test_an_empty_lock_older_than_10s_is_taken_over(folder, helper):
    """Review 261128, S2 (the owner's yes): an EMPTY lock whose byte is free
    and that is older than 10 s — a writer killed between its create and its
    marker — is taken over in place at once, not waited for until 120 s."""
    db = folder / "checksums.md5"
    lock = _lock_of(db)
    lock.write_bytes(b"")
    old = time.time() - 12
    os.utime(lock, (old, old))
    res = _run(helper, "append", db, 5, "3  empty.jxl")
    assert res["ok"] is True, res
    assert res["seconds"] < 3, res
    assert res["warned"] == [], res
    assert db.read_text(encoding="utf-8") == "3  empty.jxl\n"
    assert not lock.exists()


def test_an_empty_lock_whose_byte_is_held_is_not_taken(folder, helper):
    """Guard test: the 10 s take-over needs the byte free. An empty lock 12 s
    old whose byte another handle holds keeps the 120 s rule (fail closed)."""
    import msvcrt
    db = folder / "checksums.md5"
    lock = _lock_of(db)
    lock.write_bytes(b"")
    fd = os.open(lock, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        os.lseek(fd, 1 << 20, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        old = time.time() - 12
        os.utime(lock, (old, old))
        res = _run(helper, "append", db, 2, "4  held.jxl")
    finally:
        os.close(fd)
    assert res["ok"] is False, res
    assert not db.exists()
    assert lock.exists()


def test_release_leaves_a_lock_another_writer_took_over(folder, helper):
    lock = _lock_of(folder / "checksums.md5")
    assert _run(helper, "release", lock, "999.other", "1.mine") == {"exists": True}
    assert _run(helper, "release", lock, "1.mine", "1.mine") == {"exists": False}


def test_a_creator_that_lost_its_byte_appends_nothing(folder, helper):
    """Review 261128, S1: right after the creator's create, another writer
    takes the lock over (byte + marker) and holds it. The creator, refused
    its own byte for 2 s, used to take that for "no byte locks here" and
    append with the file alone, beside the other writer. Now: fail closed —
    no line, the other writer's lock left as it is."""
    db = folder / "checksums.md5"
    res = _run(helper, "stolen", db)
    assert res["ok"] is False, res
    assert res["db"] is False, res
    assert res["lock"] == "jxlphoto-lock 2 999.other\n", res
    assert any("NOT recorded" in w for w in res["warned"]), res


def test_a_torn_last_line_does_not_swallow_the_next(folder, helper):
    db = folder / "checksums.md5"
    db.write_bytes(b"0123abcd")
    entry = "0123456789abcdef0123456789abcdef  DSC_0001 (2).jxl"
    res = _run(helper, "append", db, 5, entry, "DSC_0001 (2).jxl")
    assert res["ok"] is True, res
    assert db.read_text(encoding="utf-8").splitlines()[-1] == entry
    assert res["read"] == "0123456789abcdef0123456789abcdef"


_CODECS = ("cjxl", "djxl", "exiftool")


@pytest.mark.skipif(not all(shutil.which(t) for t in _CODECS),
                    reason="needs cjxl, djxl and exiftool on PATH")
def test_a_deleting_run_after_a_killed_writer_files_every_line(tmp_path, folder, helper):
    """The battery's case, real codecs: a deleting JPEG -> JXL run into a
    folder whose lock a killed writer left. Pre-fix: every line dropped after
    30 s, every JPEG kept (~60 s). The run's own limit (170 s) stands for the
    plan's timeout of 180 s: pytest-timeout is not installed."""
    from PIL import Image
    src = tmp_path / "吾妻山 [lock] 100%off %d" / "元 [src] (1)"
    src.mkdir(parents=True)
    names = ("DSC_0001 (2).jpg", "菜の花 [b].jpg", "50%off %d.jpg")
    for n in names:
        Image.effect_noise((640, 480), 40).convert("RGB").save(src / n, quality=90)
    db = folder / "checksums.md5"
    holder = _spawn(helper, "hold", db, "K", 600)
    try:
        _wait_for(Path(str(db) + ".K.ready"))
    finally:
        _kill(holder)
    for p in folder.glob("checksums.md5.K.*"):
        p.unlink()
    env = dict(os.environ, JXLPHOTO_SETTINGS="")
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(SCRIPTS / "jxl_jpeg_transcoder.py"), str(src),
                        str(folder), "--mode", "0", "--workers", "2", "--force-transcode",
                        "--delete-source", "--delete-confirm-off"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=170, env=env)
    took = time.monotonic() - t0
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    assert [n for n in names if (src / n).exists()] == [], out[-3000:]
    assert sorted(p.name for p in folder.glob("*.jxl")) == sorted(
        n[:-4] + ".jxl" for n in names)
    assert len(db.read_text(encoding="utf-8").splitlines()) == 2 * len(names)
    assert not _lock_of(db).exists()
    assert "NOT recorded" not in out
    assert took < 25, took
