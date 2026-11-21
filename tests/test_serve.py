#!/usr/bin/env python3
"""--serve (v2.31.0, plan 261119 part A with the Fable review's amendments).

A backend started once reads one job per stdin line (JSON: id, input, output),
runs each exactly like a single call (`main()` with `sys.argv` set) and answers
one JSON line per job on stdout; photo-backup starts N of them instead of one
process per photo. Every test runs the script as a SUBPROCESS (the serve loop
moves file descriptors 0 and 1), in a folder with Japanese, [ ], a space and
'%'. The in-process test at the end checks that a run leaves no per-file state
behind for the next one (what a serve process relies on)."""

import ctypes
import hashlib
import json
import os
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

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import jxl_jpeg_transcoder as tr  # noqa: E402

pytestmark = [
    pytest.mark.skipif(not all(shutil.which(t) for t in ("cjxl", "djxl", "exiftool")),
                       reason="needs cjxl, djxl and exiftool"),
]

HOSTILE = "吾妻山 [x] 100%off %d"
TRANSCODE = ["--mode", "0", "--workers", "1", "--effort", "3", "--force-transcode"]
ENCODE = ["--mode", "0", "--distance", "0", "--effort", "3", "--workers", "1"]


def _env(tmp_path, **extra):
    env = dict(os.environ, JXLPHOTO_SETTINGS="", JXLPHOTO_LOG_DIR=str(tmp_path / "logs"),
               JXLPHOTO_JXL_THUMB="0", PYTHONIOENCODING="utf-8")
    env.update(extra)
    return env


def _jpeg(path, seed=0):
    from PIL import Image
    rng = np.random.default_rng(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rng.integers(0, 255, (40, 56, 3), dtype=np.uint8)).save(path, quality=90)
    return path


def _tiff(path, seed=0, pages=1):
    rng = np.random.default_rng(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffWriter(str(path)) as w:
        for _ in range(pages):
            w.write(rng.integers(0, 65535, (32, 48, 3), dtype=np.uint16), photometric="rgb",
                    software="", metadata=None)
    return path


def _job(i, inp, out):
    return json.dumps({"id": str(i), "input": str(inp), "output": str(out)}, ensure_ascii=True)


def _serve(tmp_path, script, options, lines, timeout=300, env=None):
    """(exit code, [stdout JSON objects], stderr text) of one serve process fed
    `lines` and then EOF."""
    p = subprocess.run([sys.executable, str(REPO / script), "--serve", *options],
                       input=("\n".join(lines) + ("\n" if lines else "")).encode("ascii"),
                       capture_output=True, timeout=timeout, env=env or _env(tmp_path))
    out = [json.loads(ln) for ln in p.stdout.decode("ascii").splitlines() if ln.strip()]
    return p.returncode, out, p.stderr.decode("utf-8", "replace")


def _single(tmp_path, script, inp, out, options):
    return subprocess.run([sys.executable, str(REPO / script), str(inp), str(out), *options],
                          capture_output=True, timeout=300, env=_env(tmp_path),
                          stdin=subprocess.DEVNULL).returncode


def _codestream(path):
    import jxl_photo_common as common
    return [bytes(p) for t, _h, p in common._box_list(Path(path).read_bytes())
            if t in (b"jxlc", b"jxlp")]


# ---------------------------------------------------------------------------

def test_transcoder_jobs_answer_in_order_like_single_calls(tmp_path):
    base = tmp_path / HOSTILE
    a, b = _jpeg(base / "in" / "DSC_0001 (2).jpg", 1), _jpeg(base / "in" / "写真 [b].jpg", 2)
    bad = base / "in" / "not a jpeg 50%.jpg"
    bad.write_bytes(os.urandom(4000))
    outs = []
    for name in ("o1", "o2", "o3"):
        (base / name).mkdir()
        outs.append(base / name)
    rc, res, err = _serve(tmp_path, "jxl_jpeg_transcoder.py", TRANSCODE,
                          [_job(1, a, outs[0]), _job(2, b, outs[1]), _job(3, bad, outs[2])])
    assert rc == 0, err[-2000:]
    hello, jobs, end = res[0], res[1:-1], res[-1]
    assert hello["serve"] == 1 and hello["version"] == tr.TOOLKIT_VERSION
    assert hello["script"] == "jxl_jpeg_transcoder.py"
    assert [j["id"] for j in jobs] == ["1", "2", "3"]
    assert [j["exit"] for j in jobs[:2]] == [0, 0] and jobs[2]["exit"] != 0, jobs
    assert jobs[2]["error"], jobs[2]
    assert end == {"end": True, "jobs": 3, "failed": 1}
    for src, out, j in ((a, outs[0], jobs[0]), (b, outs[1], jobs[1])):
        single = base / ("single " + out.name)
        single.mkdir()
        assert _single(tmp_path, "jxl_jpeg_transcoder.py", src, single, TRANSCODE) == 0
        name = src.stem + ".jxl"
        assert name in j["outputs"], j
        assert (out / name).read_bytes() == (single / name).read_bytes()


def test_encoder_and_decoder_jobs_match_single_calls(tmp_path):
    base = tmp_path / HOSTILE
    one = _tiff(base / "tif" / "DSC_0001 (2).tif", 1)
    two = _tiff(base / "tif2" / "scan [2] 5%.tif", 2, pages=2)
    o1, o2 = base / "jxl1", base / "jxl2"
    o1.mkdir(), o2.mkdir()
    rc, res, err = _serve(tmp_path, "jxl_tiff_encoder.py", ENCODE,
                          [_job("a", one, o1), _job("b", two, o2)])
    assert rc == 0, err[-2000:]
    assert [j["exit"] for j in res[1:-1]] == [0, 0], res
    for src, out in ((one, o1), (two, o2)):
        single = base / ("single " + out.name)
        single.mkdir()
        assert _single(tmp_path, "jxl_tiff_encoder.py", src, single, ENCODE) == 0
        made = sorted(p.name for p in out.glob("*.jxl"))
        assert made == sorted(p.name for p in single.glob("*.jxl")) and made
        for n in made:
            assert _codestream(out / n) == _codestream(single / n)
    back = base / "back [tif]"
    back.mkdir()
    rc, res, err = _serve(tmp_path, "jxl_tiff_decoder.py", ["--mode", "0", "--workers", "1"],
                          [_job("d", o2, back)])
    assert rc == 0 and res[1]["exit"] == 0, (res, err[-2000:])
    tifs = list(back.glob("*.tif"))
    assert len(tifs) == 1
    with tifffile.TiffFile(str(tifs[0])) as t, tifffile.TiffFile(str(two)) as s:
        assert [p.asarray().tolist() for p in t.pages[:2]] == [p.asarray().tolist() for p in s.pages]


def test_through_jxl_photo_py(tmp_path):
    a = _jpeg(tmp_path / HOSTILE / "in" / "DSC_0001 (2).jpg")
    out = tmp_path / HOSTILE / "out"
    out.mkdir()
    p = subprocess.run([sys.executable, str(REPO / "jxl_photo.py"), "transcode", "--serve", *TRANSCODE],
                       input=(_job(1, a, out) + "\n").encode("ascii"), capture_output=True,
                       timeout=300, env=_env(tmp_path))
    lines = [json.loads(ln) for ln in p.stdout.decode("ascii").splitlines() if ln.strip()]
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-2000:]
    assert lines[1]["exit"] == 0 and (out / "DSC_0001 (2).jxl").exists()
    assert lines[-1]["end"] is True


def test_bad_lines_are_answered_and_the_next_job_runs(tmp_path):
    a = _jpeg(tmp_path / HOSTILE / "in" / "DSC_0001 (2).jpg")
    out = tmp_path / HOSTILE / "out"
    out.mkdir()
    lines = ["not json", json.dumps({"id": "x", "input": str(a)}),
             json.dumps({"id": "y", "input": str(a), "output": str(out), "extra": 1}),
             _job("z", a, tmp_path / "no such folder"),
             _job("w", tmp_path / "missing.jpg", out),
             "", _job("ok", a, out)]
    rc, res, err = _serve(tmp_path, "jxl_jpeg_transcoder.py", TRANSCODE, lines)
    assert rc == 0, err[-2000:]
    jobs = res[1:-1]
    assert [j["exit"] for j in jobs] == [2, 2, 2, 2, 2, 0], jobs
    assert jobs[0]["id"] is None and jobs[1]["id"] == "x"
    assert all(j["error"] for j in jobs[:5])
    assert res[-1] == {"end": True, "jobs": 6, "failed": 5}


def test_one_log_for_a_serve_process(tmp_path):
    a, b = _jpeg(tmp_path / HOSTILE / "a" / "1.jpg", 1), _jpeg(tmp_path / HOSTILE / "b" / "2.jpg", 2)
    out = tmp_path / HOSTILE / "out"
    out.mkdir()
    rc, res, err = _serve(tmp_path, "jxl_jpeg_transcoder.py", TRANSCODE,
                          [_job(1, a, out), _job(2, b, out), _job(3, a, out)])
    assert rc == 0, err[-2000:]
    logs = list((tmp_path / "logs" / "jxl_jpeg_transcoder").glob("*.log"))
    assert len(logs) == 1, logs
    text = logs[0].read_text(encoding="utf-8")
    for i in (1, 2, 3):
        assert f"serve job {i}: " in text and f"serve job {i}: exit " in text


@pytest.mark.parametrize("flags", [["--delete-source"], ["--delete-skipped"], ["--clean-staging"],
                                   ["--help"], ["--version"], ["--serve"]])
def test_refused_at_the_start(tmp_path, flags):
    a = _jpeg(tmp_path / HOSTILE / "in" / "1.jpg")
    out = tmp_path / HOSTILE / "out"
    out.mkdir()
    rc, res, err = _serve(tmp_path, "jxl_jpeg_transcoder.py", TRANSCODE + flags, [_job(1, a, out)])
    assert rc == 2, (res, err[-1500:])
    assert res == [] and not list(out.iterdir())
    assert "--serve" in err


@pytest.mark.parametrize("origin,dest", [("tiff", "jxl"), ("jxl", "tiff"), ("jpeg", "jxl"),
                                         ("jxl", "jpeg")])
def test_the_wizard_refuses_serve_in_the_expert_flags(origin, dest):
    import jxl_photo as wp
    why = wp._expert_flags_problem(origin, dest, "--serve")
    assert why and "photo-backup" in why, why


def test_a_percent_in_the_temp_folder_is_refused_at_the_start(tmp_path):
    """The temp folder is the serve process's: with a '%' in it every job would
    be refused, so the process does not start (ExifTool cannot keep its own
    files there)."""
    temp = tmp_path / "tmp 100%off %d"
    temp.mkdir()
    rc, res, err = _serve(tmp_path, "jxl_jpeg_transcoder.py", TRANSCODE, [],
                          env=_env(tmp_path, TEMP=str(temp), TMP=str(temp)))
    assert rc == 2 and res == [], (res, err[-800:])
    assert "--serve cannot start: the temp folder has a '%' in its path" in err


def test_encoder_maintenance_modes_are_refused(tmp_path):
    for flags in (["--restamp-moved"], ["--restamp-then-sync"], ["--clear-icc-cache"],
                  ["--provenance", "adopt"]):
        rc, res, err = _serve(tmp_path, "jxl_tiff_encoder.py", ENCODE + flags, [])
        assert rc == 2 and res == [], (flags, err[-800:])


def test_a_file_changed_between_jobs_is_read_again(tmp_path):
    """Same path, same size and same date, other bytes: the second job's
    master must name the new bytes (the per-file caches start empty)."""
    import jxl_photo_common as common
    src = _tiff(tmp_path / HOSTILE / "tif" / "DSC_0001 (2).tif", 1)
    st = src.stat()
    o1, o2 = tmp_path / HOSTILE / "j1", tmp_path / HOSTILE / "j2"
    o1.mkdir(), o2.mkdir()
    swap = tmp_path / "swap.py"
    swap.write_text(textwrap.dedent(f"""
        import os, numpy as np, tifffile
        p = {str(src)!r}
        rng = np.random.default_rng(99)
        with tifffile.TiffWriter(p) as w:
            w.write(rng.integers(0, 65535, (32, 48, 3), dtype=np.uint16), photometric="rgb",
                    software="", metadata=None)
        os.utime(p, ns=({st.st_atime_ns}, {st.st_mtime_ns}))
    """), encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(REPO / "jxl_tiff_encoder.py"), "--serve", *ENCODE],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         env=_env(tmp_path))
    try:
        p.stdin.write((_job(1, src, o1) + "\n").encode())
        p.stdin.flush()
        assert json.loads(p.stdout.readline())["serve"] == 1
        assert json.loads(p.stdout.readline())["exit"] == 0
        subprocess.run([sys.executable, str(swap)], check=True)
        assert src.stat().st_size == st.st_size and src.stat().st_mtime_ns == st.st_mtime_ns
        p.stdin.write((_job(2, src, o2) + "\n").encode())
        p.stdin.flush()
        assert json.loads(p.stdout.readline())["exit"] == 0
        p.stdin.close()
        assert json.loads(p.stdout.readline())["end"] is True
    finally:
        p.wait(timeout=60)
    import hashlib
    outer = hashlib.sha256(hashlib.sha256(src.read_bytes()).digest()).hexdigest()[:16]
    import jxl_tiff_encoder as enc
    rel2 = enc._read_source_markers_batch([o2 / "DSC_0001 (2).jxl"])[str(o2 / "DSC_0001 (2).jxl")]
    rel1 = enc._read_source_markers_batch([o1 / "DSC_0001 (2).jxl"])[str(o1 / "DSC_0001 (2).jxl")]
    assert rel2["srcsum"] == outer and rel1["srcsum"] != outer, (rel1, rel2)


def test_no_job_only_the_end_line(tmp_path):
    rc, res, err = _serve(tmp_path, "jxl_jpeg_transcoder.py", TRANSCODE, [])
    assert rc == 0, err
    assert res[0]["serve"] == 1 and res[1:] == [{"end": True, "jobs": 0, "failed": 0}]


def _fake_backend(tmp_path, body):
    """A script that serves with the transcoder's namespace and a main() of the
    test's own (an exception, a Ctrl+C)."""
    f = tmp_path / "fake_backend.py"
    f.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(REPO)!r})
        import jxl_jpeg_transcoder as tr
        import jxl_photo_common as common
        def main():
        {textwrap.indent(textwrap.dedent(body), '    ')}
        sys.exit(common._serve(tr._G, main, ["--serve", "--mode", "0"]))
    """), encoding="utf-8")
    return f


@pytest.mark.parametrize("body, code, last", [
    ("raise RuntimeError('boom here')", 2, "RuntimeError: boom here"),
    ("raise KeyboardInterrupt()", 130, None),
    ("raise SystemExit('bad thing')", 0, "bad thing"),
])
def test_what_escapes_main(tmp_path, body, code, last):
    a = _jpeg(tmp_path / HOSTILE / "in" / "1.jpg")
    out = tmp_path / HOSTILE / "out"
    out.mkdir()
    fake = _fake_backend(tmp_path, body)
    p = subprocess.run([sys.executable, str(fake)],
                       input=(_job(1, a, out) + "\n" + _job(2, a, out) + "\n").encode(),
                       capture_output=True, timeout=120, env=_env(tmp_path))
    res = [json.loads(ln) for ln in p.stdout.decode().splitlines() if ln.strip()]
    assert p.returncode == code, (res, p.stderr.decode("utf-8", "replace")[-1500:])
    if code == 2:            # an unexpected exception: answered, then the process ends
        assert len(res) == 2 and res[1]["exit"] == 2 and last in res[1]["error"]
    elif code == 130:        # Ctrl+C: no answer, no end line
        assert len(res) == 1
    else:                    # SystemExit("text"): exit 1 with the text, like a single call
        assert [r["exit"] for r in res[1:3]] == [1, 1] and last in res[1]["error"]
        assert res[-1]["end"] is True


def _alive(pid) -> bool:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000, False, pid)
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == 0x102
    finally:
        k32.CloseHandle(h)


def _children(pid):
    """The pids whose parent is `pid` (Toolhelp snapshot)."""
    from ctypes import wintypes

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    out = []
    e = PE()
    e.dwSize = ctypes.sizeof(PE)
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        if e.th32ParentProcessID == pid:
            out.append((e.th32ProcessID, e.szExeFile.decode(errors="replace")))
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


@pytest.mark.skipif(sys.platform != "win32", reason="the Job Object is Windows")
def test_killing_the_serve_process_ends_its_children_and_the_caller_sees_eof(tmp_path):
    from PIL import Image
    big = tmp_path / HOSTILE / "in" / "big 写真.jpg"
    big.parent.mkdir(parents=True)
    Image.fromarray(np.random.default_rng(5).integers(0, 255, (3000, 4000, 3), dtype=np.uint8)
                    ).save(big, quality=95)
    out = tmp_path / HOSTILE / "out"
    out.mkdir()
    p = subprocess.Popen([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), "--serve",
                          "--mode", "0", "--workers", "1", "--effort", "9", "--force-transcode"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         env=_env(tmp_path))
    assert json.loads(p.stdout.readline())["serve"] == 1
    p.stdin.write((_job(1, big, out) + "\n").encode())
    p.stdin.flush()
    kids = []
    for _ in range(200):
        kids = [k for k in _children(p.pid) if "cjxl" in k[1].lower()]
        if kids:
            break
        time.sleep(0.05)
    assert kids, "cjxl never started"
    t0 = time.monotonic()
    p.kill()
    assert p.stdout.read() == b""                   # EOF at once: nothing holds the pipe
    assert time.monotonic() - t0 < 10
    for _ in range(100):
        if not any(_alive(k[0]) for k in kids):
            break
        time.sleep(0.1)
    assert not any(_alive(k[0]) for k in kids), "cjxl outlived the serve process"


def test_the_serve_process_ends_when_its_caller_closes_stdin(tmp_path):
    p = subprocess.Popen([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), "--serve", *TRANSCODE],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         env=_env(tmp_path))
    assert json.loads(p.stdout.readline())["serve"] == 1
    p.stdin.close()
    assert p.wait(timeout=30) == 0


def test_stderr_nobody_reads_does_not_stop_it(tmp_path):
    a = _jpeg(tmp_path / HOSTILE / "in" / "DSC_0001 (2).jpg")
    lines = []
    for i in range(30):
        o = tmp_path / HOSTILE / f"o{i}"
        o.mkdir()
        lines.append(_job(i, a, o))
    p = subprocess.Popen([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), "--serve", *TRANSCODE],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=_env(tmp_path))
    try:
        p.stdin.write(("\n".join(lines) + "\n").encode())
        p.stdin.close()
        out = []
        while True:
            ln = p.stdout.readline()
            if not ln:
                break
            out.append(json.loads(ln))
        assert p.wait(timeout=300) == 0
    finally:
        if p.poll() is None:
            p.kill()
    assert out[-1] == {"end": True, "jobs": 30, "failed": 0}


def _data(v, depth=0):
    """A stable text of a module global's VALUE, or None for what is not data
    (functions, modules, classes, locks, loggers, pools)."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return repr(v)
    if isinstance(v, bytes):
        return "bytes:" + hashlib.sha256(v).hexdigest()[:16]
    if isinstance(v, Path):
        return "Path:" + str(v)
    if depth > 4:
        return "..."
    if isinstance(v, (list, tuple)):
        return f"{type(v).__name__}[" + ",".join(str(_data(x, depth + 1)) for x in v) + "]"
    if isinstance(v, (set, frozenset)):
        return "set[" + ",".join(sorted(str(_data(x, depth + 1)) for x in v)) + "]"
    if isinstance(v, dict):
        return "dict[" + ",".join(sorted(f"{_data(a, depth + 1)}:{_data(b, depth + 1)}"
                                         for a, b in v.items())) + "]"
    return None


def _state(mod):
    return {k: d for k, v in vars(mod).items()
            if not k.startswith("__") for d in [_data(v)] if d is not None}


def _three_inputs(kind, base, tmp_path):
    if kind == "transcoder":
        return [_jpeg(base / n / f"{n}.jpg", i) for i, n in enumerate("ABC")]
    if kind == "encoder":
        return [_tiff(base / n / f"{n}.tif", i + 1) for i, n in enumerate("ABC")]
    folders = []
    for i, n in enumerate("ABC"):
        t = _tiff(base / f"t{n}" / f"{n}.tif", i + 1)
        m = base / f"m{n}"
        m.mkdir(parents=True)
        assert _single(tmp_path, "jxl_tiff_encoder.py", t, m, ENCODE) == 0
        folders.append(m)
    return folders


@pytest.mark.parametrize("kind", ["transcoder", "encoder", "decoder"])
def test_a_job_leaves_no_state_for_the_next(tmp_path, monkeypatch, kind):
    """What a serve process relies on. Job B is run twice, the same way, once
    after job A and once after job C: every data global of the script (and of
    the common module) must then hold the same value, whatever ran before —
    the per-file caches the serve loop empties (_SERVE_FILE_CACHES) included.
    Only the facts about the tools (_SERVE_TOOL_FACTS) may stay. The
    transcoder's abort latch, a scalar, was one that stayed (review S2)."""
    import jxl_jpeg_transcoder as tr_mod
    import jxl_tiff_decoder as dec_mod
    import jxl_tiff_encoder as enc_mod
    import jxl_photo_common as common
    mod, options = {"transcoder": (tr_mod, TRANSCODE), "encoder": (enc_mod, ENCODE),
                    "decoder": (dec_mod, ["--mode", "0", "--workers", "1"])}[kind]
    base = tmp_path / HOSTILE
    a, b, c = _three_inputs(kind, base, tmp_path)
    # One log for every job, as in a serve process (setup_logger hands it back).
    monkeypatch.setattr(mod, "_SERVE_LOG", tmp_path / "serve.log")

    def job(inp, out):
        out.mkdir(parents=True, exist_ok=True)
        raw = json.dumps({"id": "x", "input": str(inp), "output": str(out)}).encode()
        result, fatal = common._serve_job(mod._G, mod.main, raw, options, "log")
        assert result["exit"] == 0 and not fatal, result

    ob = base / "out B"
    job(a, base / "out A")
    job(b, ob)
    first = {**_state(mod), **{"common." + k: v for k, v in _state(common).items()}}
    shutil.rmtree(ob)
    job(c, base / "out C")
    job(b, ob)
    second = {**_state(mod), **{"common." + k: v for k, v in _state(common).items()}}
    allowed = set(mod._SERVE_TOOL_FACTS)
    leaked = {k: (first.get(k), second.get(k)) for k in set(first) | set(second)
              if first.get(k) != second.get(k) and k not in allowed}
    assert not leaked, f"state a job leaves for the next one: {leaked}"


def test_a_latched_abort_is_not_the_next_job_s(tmp_path):
    """Review S2: the disk-full abort latch of a job before answered a later
    job that returned early (an input folder with no JPEG) with exit 2."""
    import jxl_photo_common as common
    empty = tmp_path / HOSTILE / "no jpeg here"
    empty.mkdir(parents=True)
    out = tmp_path / HOSTILE / "out"
    out.mkdir()
    raw = json.dumps({"id": "1", "input": str(empty), "output": str(out)}).encode()
    tr._signal_abort("the volume is full (planted)")
    try:
        result, _fatal = common._serve_job(tr._G, tr.main, raw, TRANSCODE, "log")
    finally:
        tr._reset_abort()
    assert result["exit"] == 0, result


def test_options_that_fail_every_job_never_stop_the_process(tmp_path):
    """Review M1: a check main() makes itself (here --resize-long 0) printed
    argparse's usage on the caller's stderr for every job; with stderr never
    read the process stopped after 1 answer of 40, and the answer only said
    "see the log", which did not hold the reason."""
    a = _jpeg(tmp_path / HOSTILE / "in" / "DSC_0001 (2).jpg")
    lines = []
    for i in range(40):
        o = tmp_path / HOSTILE / f"o{i}"
        o.mkdir()
        lines.append(_job(i, a, o))
    p = subprocess.Popen([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), "--serve",
                          "--mode", "0", "--workers", "1", "--force-convert",
                          "--resize-long", "0"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=_env(tmp_path))
    out = []

    def feed():
        try:
            p.stdin.write(("\n".join(lines) + "\n").encode())
            p.stdin.close()
        except OSError:
            pass

    def read():
        for ln in p.stdout:          # stderr is NEVER read, on purpose
            out.append(json.loads(ln))

    threads = [threading.Thread(target=feed, daemon=True),
               threading.Thread(target=read, daemon=True)]
    for t in threads:
        t.start()
    try:
        threads[1].join(240)
        assert not threads[1].is_alive(), f"stopped after {len(out) - 1} answer(s) of 40"
        assert p.wait(timeout=60) == 0
    finally:
        if p.poll() is None:
            p.kill()
    jobs = out[1:-1]
    assert out[-1] == {"end": True, "jobs": 40, "failed": 40}, out[-1]
    assert all(j["exit"] == 2 and "--resize-long" in j["error"] for j in jobs), jobs[:2]
    log = next((tmp_path / "logs").rglob("*.log")).read_text(encoding="utf-8")
    assert "--resize-long must be a positive number of pixels" in log


def test_a_job_s_own_input_error_is_in_its_answer_and_the_log(tmp_path):
    """Review M1, the other case: decoder --mode 6 with a FILE as the job's
    input fails in main()'s own check — the reason reaches the answer."""
    src = _tiff(tmp_path / HOSTILE / "tif" / "DSC_0001 (2).tif", 1)
    masters = tmp_path / HOSTILE / "m"
    masters.mkdir()
    assert _single(tmp_path, "jxl_tiff_encoder.py", src, masters, ENCODE) == 0
    jxl = next(masters.glob("*.jxl"))
    back = tmp_path / HOSTILE / "back"
    back.mkdir()
    rc, res, err = _serve(tmp_path, "jxl_tiff_decoder.py", ["--mode", "6", "--workers", "1"],
                          [_job("d", jxl, back)])
    assert rc == 0 and err == "", (res, err[-800:])
    assert res[1]["exit"] == 2 and "DIRECTORY" in res[1]["error"], res[1]


@pytest.mark.parametrize("bad", ["", "relative folder"])
def test_an_empty_or_relative_path_is_refused(tmp_path, bad):
    """Review S1: an empty string meant the serve process's working folder;
    a job with both empty converted every JPEG found there."""
    a = _jpeg(tmp_path / HOSTILE / "in" / "DSC_0001 (2).jpg")
    out = tmp_path / HOSTILE / "out"
    out.mkdir()
    lines = [json.dumps({"id": "i", "input": bad, "output": str(out)}),
             json.dumps({"id": "o", "input": str(a), "output": bad}),
             _job("ok", a, out)]
    rc, res, err = _serve(tmp_path, "jxl_jpeg_transcoder.py", TRANSCODE, lines)
    assert rc == 0, err[-1500:]
    jobs = res[1:-1]
    assert [j["exit"] for j in jobs] == [2, 2, 0], jobs
    assert all("absolute path" in j["error"] for j in jobs[:2]), jobs


def test_a_bom_line_runs_and_a_line_too_deep_is_answered(tmp_path):
    """PowerShell's Out-File starts a file with a BOM; a line of 100000 '['
    raised RecursionError past the loop (no answer, no end line)."""
    a = _jpeg(tmp_path / HOSTILE / "in" / "DSC_0001 (2).jpg")
    out = tmp_path / HOSTILE / "out"
    out.mkdir()
    payload = (b"\xef\xbb\xbf" + _job("bom", a, out).encode() + b"\n"
               + b"[" * 100000 + b"\n" + _job("after", a, out).encode() + b"\n")
    p = subprocess.run([sys.executable, str(REPO / "jxl_jpeg_transcoder.py"), "--serve",
                        *TRANSCODE], input=payload, capture_output=True, timeout=300,
                       env=_env(tmp_path))
    res = [json.loads(ln) for ln in p.stdout.decode("ascii").splitlines() if ln.strip()]
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-1500:]
    jobs = res[1:-1]
    assert [(j["id"], j["exit"]) for j in jobs] == [("bom", 0), (None, 2), ("after", 0)], jobs
    assert res[-1] == {"end": True, "jobs": 3, "failed": 1}
