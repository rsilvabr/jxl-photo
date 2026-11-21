#!/usr/bin/env python3
"""EXIFTOOL_STAY_OPEN (v2.31.0): the per-file ExifTool commands go to an
ExifTool kept open (-stay_open True -@ -) instead of a process each.

A fresh ExifTool costs ~0.2 s on Windows (197 ms a command, measured
2026-11-21) and a 45 MP two-page TIFF started nine; the kept-open one answers
in ~15 ms. These tests pin what must not change — the same status, stdout and
stderr as a process of its own, byte for byte for a binary answer — and the
rules of the kept-open process: one for many commands, fail closed when it
ends, killed on a timeout, never outliving Python (Job Object), started per
working directory, a file it wrote free at once. Real ExifTool, in a folder
with Japanese, [ ], a space and '%'. Windows only (the feature is)."""

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jxl_photo_common as common
import jxl_tiff_encoder as enc

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="EXIFTOOL_STAY_OPEN is Windows only"),
    pytest.mark.skipif(shutil.which("exiftool") is None, reason="needs exiftool"),
]

HOSTILE = "吾妻山 [x] 100%off %d"


@pytest.fixture
def pool(monkeypatch):
    """A fresh pool for the encoder, closed after the test."""
    p = common._ExiftoolPool()
    monkeypatch.setattr(enc, "_EXIFTOOL_POOL", p)
    monkeypatch.setattr(enc, "EXIFTOOL_STAY_OPEN", True)
    yield p
    p.close_all()


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / HOSTILE
    d.mkdir()
    return d


def _tiff(path, caption="写真 caption"):
    tifffile.imwrite(str(path), np.full((16, 24, 3), 1000, np.uint16), photometric="rgb")
    r = _old_way(["-overwrite_original", f"-XMP-dc:Description={caption}",
                  "-XMP-dc:Relation+=jxlphoto-test:1", "-Artist=山田", str(path)])
    assert r.returncode == 0, r.stderr
    return path


def _old_way(lines, timeout=60):
    """One ExifTool process for the command, as before v2.31.0."""
    saved = enc.EXIFTOOL_STAY_OPEN
    enc.EXIFTOOL_STAY_OPEN = False
    try:
        return enc._run_exiftool_argfile(lines, timeout=timeout)
    finally:
        enc.EXIFTOOL_STAY_OPEN = saved


def test_one_kept_open_process_answers_many_commands(pool, folder):
    t = _tiff(folder / "DSC 0001 (2).tif")
    for _ in range(12):
        r = enc._run_exiftool_argfile(["-j", "-XMP-dc:Description", str(t)], timeout=60)
        assert r.returncode == 0
        assert json.loads(r.stdout)[0]["Description"] == "写真 caption"
    assert pool.started == 1


@pytest.mark.parametrize("lines", [
    ["-j", "-XMP-dc:Relation", "-Artist", "{t}"],
    ["-s3", "-XMP-dc:Description", "{t}"],
    ["-s", "-s", "-s", "-IFD0:Software", "{t}"],
    ["-j", "{folder}/missing file.tif"],
    ["-Nonsense:Tag", "{t}"],
])
def test_the_kept_open_answer_is_the_process_answer(pool, folder, lines):
    t = _tiff(folder / "DSC 0001 (2).tif")
    lines = [a.format(t=t, folder=folder) for a in lines]
    old = _old_way(lines)
    new = enc._run_exiftool_argfile(lines, timeout=60)
    assert pool.started == 1
    assert (new.returncode, new.stdout, new.stderr) == (old.returncode, old.stdout, old.stderr)


def test_a_write_is_the_same_and_the_file_is_free_at_once(pool, folder):
    a = _tiff(folder / "a 100%.tif")
    b = folder / "b 100%.tif"
    shutil.copy2(a, b)
    write = ["-overwrite_original", "-XMP-dc:Title=タイトル [1]", "-XMP-dc:Relation+=jxlphoto-x:2"]
    assert _old_way(write + [str(a)]).returncode == 0
    r = enc._run_exiftool_argfile(write + [str(b)], timeout=60)
    assert r.returncode == 0, r.stderr
    # ExifTool kept open must not hold the file: rename and delete it now.
    moved = folder / "b moved.tif"
    os.replace(b, moved)
    read = ["-j", "-XMP-dc:Title", "-XMP-dc:Relation"]
    assert (json.loads(_old_way(read + [str(a)]).stdout)[0]["Title"]
            == json.loads(_old_way(read + [str(moved)]).stdout)[0]["Title"] == "タイトル [1]")
    moved.unlink()
    assert pool.started == 1


def test_a_binary_answer_is_byte_for_byte(pool, folder, tmp_path):
    from PIL import Image
    jpg = folder / "写真 1.jpg"
    exif = Image.Exif()
    exif[0x010F] = "Maker"
    exif[0x0110] = "Model\nwith\r\nnewlines"
    Image.fromarray(np.full((8, 8, 3), 90, np.uint8)).save(jpg, exif=exif)
    af = tmp_path / "exif.args"
    af.write_text(f"{enc._ARGFILE_CHARSET}-b\n-Exif\n{jpg}\n", encoding="utf-8")
    saved = enc.EXIFTOOL_STAY_OPEN
    enc.EXIFTOOL_STAY_OPEN = False
    try:
        old = enc._run_exiftool_file(af, 60, text=False)
    finally:
        enc.EXIFTOOL_STAY_OPEN = saved
    new = enc._run_exiftool_file(af, 60, text=False)
    assert pool.started == 1
    assert len(old.stdout) > 8 and new.stdout == old.stdout


def _encode(src_dir, out_dir=None, extra=()):
    old_argv = sys.argv
    sys.argv = (["jxl_tiff_encoder.py", str(src_dir)]
                + ([str(out_dir), "--mode", "0"] if out_dir else ["--mode", "1"])
                + ["--workers", "1", "--distance", "1", "--effort", "1", "--no-preflight"]
                + list(extra))
    try:
        with pytest.raises(SystemExit) as e:
            enc.main()
            raise SystemExit(0)
        assert e.value.code in (0, None)
    finally:
        sys.argv = old_argv


def test_the_encoder_reads_through_one_process(pool, folder, monkeypatch):
    """A whole encode: every per-file command on the kept-open process — but
    the -ver probe (once), the planning-time batch read and a command that
    changes the XMP prefixes of the ones after it (here the MIE dump of a
    '%' path): each a process of its own."""
    _tiff(folder / "DSC 0001 (2).tif")
    starts = []
    real = enc._run_captured

    def spy(cmd, *a, **k):
        if cmd and "exiftool" in str(cmd[0]).lower():
            args = [str(c) for c in cmd[1:]]
            if "-@" in args:
                args = Path(args[args.index("-@") + 1]).read_text(encoding="utf-8").splitlines()
            starts.append((args, k.get("stall") is not None))
        return real(cmd, *a, **k)

    # A pass-through spy that keeps the kept-open process ON: it says it is
    # the runner of the script (a double of another module turns it off, by
    # design: the double must see every command).
    spy.__module__ = enc.__name__
    monkeypatch.setattr(enc, "_run_captured", spy)
    _encode(folder)
    out = folder / enc.CONVERTED_JXL_FOLDER / "DSC 0001 (2).jxl"
    assert out.exists()
    per_file = [a for a, batch in starts
                if not batch and "-ver" not in a and not common._taints_xmp_prefixes(a)]
    assert per_file == [], per_file
    assert any(common._taints_xmp_prefixes(a) for a, _b in starts)     # the '%' path's dump
    assert pool.started == 1


def test_a_test_double_still_sees_every_command(pool, folder, monkeypatch):
    t = _tiff(folder / "DSC 0001 (2).tif")
    seen = []
    real = enc._run_captured

    def double(cmd, *a, **k):
        seen.append(cmd)
        return real(cmd, *a, **k)

    monkeypatch.setattr(enc, "_run_captured", double)
    r = enc._run_exiftool_argfile(["-j", "-Artist", str(t)], timeout=60)
    assert r.returncode == 0
    assert seen and pool.started == 0


def test_a_subprocess_double_installed_after_a_real_call_sees_every_command(
        pool, folder, monkeypatch):
    """A kept-open process started by an earlier real call must not answer
    behind the back of a double of subprocess installed later (review nit)."""
    t = _tiff(folder / "DSC 0001 (2).tif")
    assert enc._run_exiftool_argfile(["-j", "-Artist", str(t)], timeout=60).returncode == 0
    assert pool.started == 1 and len(pool.idle) == 1
    seen = []
    real_run = subprocess.run

    def double(cmd, *a, **k):
        seen.append(cmd)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", double)
    r = enc._run_exiftool_argfile(["-j", "-Artist", str(t)], timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout)[0]["Artist"] == "山田"
    assert any("exiftool" in str(c[0]).lower() for c in seen), seen


def test_workers_that_ask_at_once_probe_exiftool_once(monkeypatch):
    """The recompressor's six workers each started a -ver process at the
    start of a run: the probe was not ordered (found after the review)."""
    calls = []

    def probe(g, commands, timeout):
        calls.append(commands)
        time.sleep(0.3)                     # long enough for every thread to ask
        return [subprocess.CompletedProcess(commands[0], 0, "13.59\n", "")]

    monkeypatch.setattr(common, "_exiftool_one_process", probe)
    enc._EXIFTOOL_BATCH.clear()
    barrier = threading.Barrier(6, timeout=20)
    answers = []

    def ask():
        barrier.wait()
        answers.append(common._exiftool_batch_ok(enc._G))

    threads = [threading.Thread(target=ask) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert answers == [True] * 6
    assert len(calls) == 1, f"{len(calls)} probes"


def test_off_runs_a_process_per_command(pool, folder, monkeypatch):
    monkeypatch.setattr(enc, "EXIFTOOL_STAY_OPEN", False)
    t = _tiff(folder / "DSC 0001 (2).tif")
    for _ in range(3):
        assert enc._run_exiftool_argfile(["-j", "-Artist", str(t)], timeout=60).returncode == 0
    assert pool.started == 0


def test_a_process_that_died_while_idle_is_replaced(pool, folder):
    t = _tiff(folder / "DSC 0001 (2).tif")
    assert enc._run_exiftool_argfile(["-j", "-Artist", str(t)], timeout=60).returncode == 0
    assert len(pool.idle) == 1
    pool.idle[0].proc.kill()
    pool.idle[0].proc.wait()
    r = enc._run_exiftool_argfile(["-j", "-Artist", str(t)], timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout)[0]["Artist"] == "山田"
    assert pool.started == 2


def test_each_working_directory_gets_its_own_process(pool, tmp_path, monkeypatch):
    a, b = tmp_path / "a [1]", tmp_path / "b 写真"
    a.mkdir()
    b.mkdir()
    _tiff(a / "x.tif", caption="from a")
    _tiff(b / "x.tif", caption="from b")
    monkeypatch.chdir(a)
    ra = enc._run_exiftool_argfile(["-s3", "-XMP-dc:Description", "x.tif"], timeout=60)
    monkeypatch.chdir(b)
    rb = enc._run_exiftool_argfile(["-s3", "-XMP-dc:Description", "x.tif"], timeout=60)
    assert (ra.stdout.strip(), rb.stdout.strip()) == ("from a", "from b")
    assert pool.started == 2


def _fake_server(tmp_path, body):
    script = tmp_path / "fake_exiftool.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return lambda exe: subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)


def test_a_timeout_kills_the_process_and_raises(pool, tmp_path, monkeypatch):
    monkeypatch.setattr(common, "_spawn_exiftool_server", _fake_server(tmp_path, """
        import sys, time
        sys.stdin.buffer.readline()
        time.sleep(120)
    """))
    srv = pool.acquire("exiftool")
    assert srv is not None
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        srv.run(["-ver"], time.monotonic() + 1.0, 1.0)
    assert time.monotonic() - t0 < 10
    assert srv.proc.poll() is not None


def test_a_command_left_unanswered_fails_closed(pool, tmp_path, monkeypatch, folder):
    """ExifTool gone after it got the command: status -1, never run again."""
    monkeypatch.setattr(common, "_spawn_exiftool_server", _fake_server(tmp_path, """
        import sys
        for line in sys.stdin.buffer:
            if line.strip().startswith(b"-execute"):
                break
    """))
    t = _tiff(folder / "DSC 0001 (2).tif")
    r = enc._run_exiftool_argfile(["-overwrite_original", "-Artist=x", str(t)], timeout=30)
    assert r.returncode == -1
    assert "kept-open ExifTool ended" in r.stderr
    assert pool.started == 1


def _alive(pid) -> bool:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == 0x102   # WAIT_TIMEOUT: still running
    finally:
        k32.CloseHandle(h)


def test_the_kept_open_exiftool_ends_with_python(tmp_path):
    """Python killed (TerminateProcess, no atexit): its ExifTool goes too —
    the Job Object; ExifTool alone never ends (measured)."""
    t = _tiff(tmp_path / "x 写真.tif")
    child = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(Path(enc.__file__).parent)!r})
            import jxl_tiff_encoder as enc
            r = enc._run_exiftool_argfile(["-j", "-Artist", {str(t)!r}], timeout=60)
            assert r.returncode == 0, r.stderr
            print(enc._EXIFTOOL_POOL.idle[0].proc.pid, flush=True)
            time.sleep(120)
        """)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    try:
        line = child.stdout.readline()
        pid = int(line.strip())
        assert _alive(pid)
        child.kill()
        child.wait()
        for _ in range(50):
            if not _alive(pid):
                break
            time.sleep(0.1)
        assert not _alive(pid), "the kept-open ExifTool outlived the Python that started it"
    finally:
        if child.poll() is None:
            child.kill()


def _end_pid(pid) -> None:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x0001, False, pid)            # PROCESS_TERMINATE
    if h:
        k32.TerminateProcess(h, 1)
        k32.CloseHandle(h)


def test_a_kill_ends_a_launcher_s_child_too(pool, tmp_path, monkeypatch):
    """Review S6: some ExifTool installs run as a launcher and a child (the
    PAR-packed exiftool.exe before 12.88, a Chocolatey shim). A timeout or a
    dead pipe must end the child too, not only the process Python started —
    else it keeps running its command, holding the file it writes."""
    d = tmp_path / HOSTILE
    d.mkdir()
    pidfile = d / "child.pid"
    child = d / "child.py"
    child.write_text("import os, sys, time\n"
                     "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
                     "time.sleep(120)\n", encoding="utf-8")
    launcher = d / "launcher.py"
    launcher.write_text(textwrap.dedent(f"""
        import subprocess, sys
        subprocess.Popen([sys.executable, {str(child)!r}, {str(pidfile)!r}]).wait()
    """), encoding="utf-8")
    monkeypatch.setattr(common, "_spawn_exiftool_server", lambda exe: subprocess.Popen(
        [sys.executable, str(launcher)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, bufsize=0))
    srv = pool.acquire("exiftool")
    assert srv is not None
    pid = None
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            pid = int(pidfile.read_text())
            break
        time.sleep(0.1)
    assert pid and _alive(pid)
    try:
        pool.drop(srv)                     # what a timeout or a dead pipe does
        for _ in range(50):
            if not _alive(pid):
                break
            time.sleep(0.1)
        assert not _alive(pid), "the launcher's child outlived the kill"
    finally:
        if _alive(pid):
            _end_pid(pid)


def test_idle_processes_are_capped_and_the_last_used_comes_first(monkeypatch):
    """Review S4: idle processes were kept up to the most ever busy (28 with
    the sRGB preset's workers, ~50 MB each outside the memory budget), and
    taken first-in first-out, so none ever aged out."""
    class Fake:
        def __init__(self, exe, *a):
            self.exe, self.cwd = exe, os.getcwd()
            self.commands, self.last_used, self.closed = 0, time.monotonic(), False

        def alive(self):
            return not self.closed

        def close(self):
            self.closed = True

        kill = close

    monkeypatch.setattr(common, "_ExiftoolServer", Fake)
    p = common._ExiftoolPool()
    servers = [p.acquire("exiftool") for _ in range(12)]
    assert all(servers) and p.peak == 12
    for s in servers:
        p.release(s)
    assert len(p.idle) == 8
    assert sum(s.closed for s in servers) == 4
    assert p.acquire("exiftool") is servers[7]          # the last one kept


@pytest.mark.parametrize("lines,taints", [
    (["-o", "x.mie", "-all:all", "-exif:all#", "-unsafe", "a 100%.tif"], True),
    (["-overwrite_original", "-tagsfromfile", "a.tif", "-exif:all#", "-xmp:all", "b.jxl"], True),
    (["-tagsfromfile", "a.tif", "-XMP-dc:all", "b.jxl"], True),
    (["-tagsfromfile", "a.tif", "-all", "b.jpg"], True),
    (["-o", "b.xmp", "-tagsfromfile", "a.jxl", "-XMP"], False),
    (["-overwrite_original", "-XMP<=b 100%.xmp", "c.tif"], False),
    (["-overwrite_original", "-tagsfromfile", "a.jxl", "-exif:all#", "-iptc:all", "c.tif"], False),
    (["-overwrite_original", "-XMP-dc:Relation=", "-XMP-dc:Relation+=x", "-xmp:all=", "c.tif"],
     False),
    (["-overwrite_original", "-tagsfromfile", "@", "-XMP-dc:Description<IPTC:Caption-Abstract",
      "c.tif"], False),
    (["-overwrite_original", "-tagsfromfile", "a.jxl", "--xmp:all", "-exif:all#", "c.tif"], False),
    (["-o", "x.mie", "-iptc:all", "a 100%.tif"], False),
    (["-j", "-XMP:all", "-G1", "a.tif"], False),
    # a struct write other than xmpMM:History (Fable's review, S1)
    (["-overwrite_original", "-XMP-xmp:Thumbnails={Width=7}", "x.xmp"], True),
    (["-overwrite_original", "-XMP-xmpMM:History+={Action=converted,When=2026}", "x.jxl"],
     False),
    (["-overwrite_original", "-XMP-dc:Description=a={b}", "x.jxl"], False),
])
def test_which_commands_change_the_xmp_prefixes(lines, taints):
    assert common._taints_xmp_prefixes(lines) is taints


def test_the_history_append_then_its_reads_match_a_fresh_process(pool, folder):
    """Fable's review, S1: after a struct write a kept-open ExifTool names that
    struct's flattened tags differently (Thumbnails={...}, then ThumbnailWidth
    read back EMPTY with status 0). The one struct the scripts write is
    xmpMM:History, and the reads they make of it must answer exactly as a
    fresh process does, the append first in the process."""
    t = _tiff(folder / "DSC 0001 (2).tif")
    hist = enc._history_args("converted", t, t, record="gen=1 | cjxl d=0.1 e=7")
    w = enc._run_exiftool_argfile(["-overwrite_original"] + hist + [str(t)], timeout=60)
    assert w.returncode == 0, w.stderr
    for lines in (["-s", "-s", "-s", "-XMP-xmpMM:HistoryParameters", str(t)],
                  ["-j", "-XMP-dc:Relation", "-XMP-dc:Description", "-EXIF:Software",
                   "-XMP-xmpMM:HistoryParameters", str(t)],
                  ["-j", "-struct", "-XMP-xmpMM:History", str(t)]):
        new = enc._run_exiftool_argfile(lines, timeout=60)
        old = _old_way(lines)
        assert (new.returncode, new.stdout, new.stderr) == (old.returncode, old.stdout, old.stderr)
    assert pool.started == 1


def _hold_without_sharing(path):
    """Open `path` with no sharing at all, as an antivirus, an indexer or a
    backup agent does for an instant to a file just written."""
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)   # GENERIC_READ, OPEN_EXISTING
    assert h not in (None, 0, -1, 2 ** 64 - 1), ctypes.get_last_error()
    return lambda: k32.CloseHandle(h)


@pytest.mark.parametrize("stay_open", [True, False])
def test_a_file_exiftool_cannot_open_is_a_failed_command(pool, tmp_path, monkeypatch,
                                                         stay_open):
    """Fable's review, M1: ExifTool answers a -TAG<=FILE that exists but that
    it cannot open (held by another program) with "Error opening file" and
    exit status 0, and the output lacks all that file held (a decoded TIFF
    without its XMP and its jxlphoto-src reported OK). A file that does not
    exist was already an error."""
    monkeypatch.setattr(enc, "EXIFTOOL_STAY_OPEN", stay_open)
    d = tmp_path / "吾妻山 [b] plain"
    d.mkdir()
    t = _tiff(d / "DSC 0001 (2).tif")
    block = d / "block 写真.xmp"
    assert _old_way(["-o", str(block), "-tagsfromfile", str(t), "-XMP"]).returncode == 0
    release = _hold_without_sharing(block)
    try:
        # With another tag in the same command (the decoder's write has the
        # EXIF) the file IS updated, so ExifTool's status is 0.
        r = enc._run_exiftool_argfile(["-overwrite_original", "-IFD0:Artist=写真家",
                                       f"-XMP<={block}", str(t)], timeout=60)
    finally:
        release()
    assert "Error opening file" in r.stderr, r.stderr
    assert r.returncode != 0
    assert bool(pool.started) is stay_open


def test_a_command_exiftool_refuses_before_running_is_answered_at_once(pool, folder):
    """Fable's review, S2: an option that takes the -echo3 line as its value
    (-o as the last line) left no marker on stdout: the kept-open process
    waited the whole timeout, and ExifTool's own message was lost."""
    t0 = time.monotonic()
    r = enc._run_exiftool_argfile(["-o"], timeout=20)
    took = time.monotonic() - t0
    assert took < 10, f"{took:.1f} s"
    assert r.returncode != 0 and r.stderr.strip(), r
    # the process that refused it is retired; the next command gets another
    t = _tiff(folder / "DSC 0001 (2).tif")
    ok = enc._run_exiftool_argfile(["-j", "-Artist", str(t)], timeout=60)
    assert ok.returncode == 0 and json.loads(ok.stdout)[0]["Artist"] == "山田"
    assert pool.started == 2


_GOOGLE_XMP = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
               'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
               '<rdf:Description rdf:about="" '
               'xmlns:Container="http://ns.google.com/photos/1.0/container/" '
               'xmlns:Item="http://ns.google.com/photos/1.0/container/item/" '
               'xmlns:dc="http://purl.org/dc/elements/1.1/">'
               '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">写真</rdf:li></rdf:Alt></dc:title>'
               '<Container:Directory><rdf:Seq>'
               '<rdf:li rdf:parseType="Resource"><Container:Item Item:Mime="image/jpeg" '
               'Item:Semantic="Primary" Item:Length="0"/></rdf:li>'
               '</rdf:Seq></Container:Directory>'
               '</rdf:Description></rdf:RDF></x:xmpmeta>').encode("utf-8")


@pytest.mark.skipif(not all(shutil.which(t) for t in ("cjxl", "djxl")), reason="needs cjxl, djxl")
def test_a_master_never_depends_on_what_the_process_ran_before(pool, tmp_path):
    """Review S2: a command that writes XMP fields copied from a file (the MIE
    dump of a '%' path, a -xmp:all copy) makes ExifTool write Google's
    container item namespace `GItem:` in every later command of the same
    process, where the file and a fresh process keep `Item:`. Two copies of
    one TIFF, the first in a '%' folder: the second master came out other
    bytes than when made alone. Such commands run in a process of their own."""
    masters = []
    for name in ("吾妻山 [a] 100%off %d", "吾妻山 [b] plain"):
        src = tmp_path / name / "DSC 0001 (2).tif"
        src.parent.mkdir()
        tifffile.imwrite(str(src), np.full((16, 24, 3), 1000, np.uint16), photometric="rgb",
                         extratags=[(700, 1, len(_GOOGLE_XMP), _GOOGLE_XMP, True)],
                         software="", metadata=None)
        out = tmp_path / (name + " out")
        _encode(src.parent, out)
        masters.append(out / "DSC 0001 (2).jxl")
    assert pool.started >= 1
    for m in masters:
        data = m.read_bytes()
        assert b"http://ns.google.com/photos/1.0/container/item/" in data, m
        assert b"GItem" not in data, f"{m.parent.name}: the namespace prefix changed"


class _FakeStream:
    def __init__(self):
        self.data = b""

    def write(self, b):
        self.data += b

    def flush(self):
        pass

    def close(self):
        pass


class _FakeProc:
    def __init__(self):
        self.stdin, self.stdout, self.stderr = _FakeStream(), _FakeStream(), _FakeStream()
        self.returncode = None

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode


def _bare_server():
    """An _ExiftoolServer around a fake process: run()'s own loop, with the
    pipes read by a test's _read_some."""
    srv = object.__new__(common._ExiftoolServer)
    srv.exe, srv.cwd, srv.commands, srv.dead = "exiftool", os.getcwd(), 0, False
    srv.last_used = time.monotonic()
    srv.proc = _FakeProc()
    srv.job = None
    srv._job_lock = threading.Lock()
    srv._pipes = ((1, None), (2, None))
    return srv


def test_a_large_answer_is_read_in_linear_time(monkeypatch):
    """Review S3: the marker was searched for from the start of the answer
    after every 4 KB read of the pipe: a 32 MB -b -XMP took 9.6 s instead of
    1.4 s (a bloated Photoshop XMP read for a '%' path)."""
    srv = _bare_server()
    piece = b"<rdf:li>{jxlphoto} 0 value</rdf:li>\r\n"
    size = 16 * 2**20          # measured: 4.8 s searched from the start, 0.04 s now
    state = {}

    def read_some(fd, handle, buf):
        if not state:
            tag = re.search(rb"\{jxlphoto[0-9a-f]{12}", srv.proc.stdin.data).group(0)
            num = re.search(rb"-execute(\d+)", srv.proc.stdin.data).group(1)
            mark = tag + b" 0 0}\r\n"
            body = piece * (size // len(piece))
            state.update(out=memoryview(body + mark + b"{ready" + num + b"}\r\n"), err=mark,
                         at=0, size=len(body))
        if fd == 1:
            at = state["at"]
            if at >= len(state["out"]):
                return False
            buf += state["out"][at:at + 4096]
            state["at"] = at + 4096
            return True
        if state["err"]:
            buf += state["err"]
            state["err"] = b""
            return True
        return False

    monkeypatch.setattr(common._ExiftoolServer, "_read_some", staticmethod(read_some))
    t0 = time.monotonic()
    status, out, err = srv.run(["-b", "-XMP", "x.tif"], time.monotonic() + 120, 120)
    took = time.monotonic() - t0
    assert status == 0 and len(out) == state["size"]
    assert took < 1.0, f"{took:.1f} s for an answer of {size >> 20} MB"


def test_an_answer_that_trickles_in_without_its_marker_times_out(monkeypatch):
    """The deadline was looked at only when nothing came: an answer that kept
    trickling in never timed out (review nit)."""
    srv = _bare_server()

    def read_some(fd, handle, buf):
        if fd == 1 and len(buf) < 100000:
            buf += b"."
        return True                         # something keeps arriving

    monkeypatch.setattr(common._ExiftoolServer, "_read_some", staticmethod(read_some))
    result = {}

    def go():
        try:
            srv.run(["-ver"], time.monotonic() + 0.5, 0.5)
        except subprocess.TimeoutExpired:
            result["timeout"] = True

    th = threading.Thread(target=go, daemon=True)
    th.start()
    th.join(10)
    assert result.get("timeout"), "an answer that kept trickling in never timed out"
