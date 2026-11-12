#!/usr/bin/env python3
"""Mutation check, on a COPY of the repo: break one thing, the test must FAIL.

    python tools/mutate.py <spec.json> [--keep] [--out report.txt]

spec.json is a list of mutations (the format of tiff-workflow's mutcheck.py):

    [{"id": "M1", "file": "jxl_tiff_encoder.py",
      "old": "exact text, found exactly once", "new": "its replacement",
      "tests": ["tests/test_x.py::test_y"],
      "expect": "optional text the failing output must contain"}]

The working tree is never touched: the repo's files (tracked, plus untracked
ones that are not ignored) are copied to a scratch folder first, so a
mutation can run while the suite or the battery uses the repo. For each
mutation: its tests must PASS on the copy as it is, then FAIL with the
change (KILLED), and the file goes back to its exact bytes (sha256 checked).
A mutation that leaves the tests green SURVIVED: either the test is too weak
or the change is harmless (say which in the report).

Bytecode is neither written nor reused (-B, PYTHONDONTWRITEBYTECODE, the
__pycache__ folders removed): a .pyc is matched to its source by time and
size, and a same-second mutation of the same length would run the OLD code.

Exit status: 0 when every mutation was KILLED, 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _repo_files() -> list:
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "--cached", "--others",
                        "--exclude-standard", "-z"], capture_output=True, check=True)
    return [f for f in r.stdout.decode("utf-8").split("\0") if f]


def _copy_repo(dest: Path) -> None:
    for rel in _repo_files():
        src = REPO / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def _drop_bytecode(root: Path) -> None:
    for d in root.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def _run_tests(root: Path, tests: list) -> subprocess.CompletedProcess:
    _drop_bytecode(root)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
         "-p", "no:xdist", *tests],
        cwd=str(root), capture_output=True, env=env, stdin=subprocess.DEVNULL)


def _tail(proc: subprocess.CompletedProcess, n: int = 6) -> str:
    text = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip().splitlines()
    return "\n".join("        " + line for line in text[-n:])


def run(specs: list, work: Path) -> int:
    killed = 0
    baseline_ok = set()
    for sp in specs:
        path = work / sp["file"]
        original = path.read_bytes()
        sha = hashlib.sha256(original).hexdigest()
        text = original.decode("utf-8")
        # The working tree is CRLF: a multi-line `old`/`new` written with \n
        # is matched and written with the file's own line ending.
        newline = "\r\n" if b"\r\n" in original else "\n"
        old, new = (s.replace("\r\n", "\n").replace("\n", newline)
                    for s in (sp["old"], sp["new"]))
        found = text.count(old)
        if found != 1:
            print(f"NOT RUN   {sp['id']}: `old` occurs {found} times in {sp['file']}")
            continue
        key = tuple(sp["tests"])
        if key not in baseline_ok:
            base = _run_tests(work, sp["tests"])
            if base.returncode != 0:
                print(f"NOT RUN   {sp['id']}: the tests fail WITHOUT the mutation\n{_tail(base)}")
                continue
            baseline_ok.add(key)
        mutated = text.replace(old, new)
        try:
            path.write_bytes(mutated.encode("utf-8"))
            res = _run_tests(work, sp["tests"])
        finally:
            path.write_bytes(original)
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            print(f"ERROR     {sp['id']}: {sp['file']} did not go back to its bytes")
            return 1
        out = (res.stdout + res.stderr).decode("utf-8", "replace")
        if res.returncode == 0:
            print(f"SURVIVED  {sp['id']}: the tests still pass with the mutation")
        elif sp.get("expect") and sp["expect"] not in out:
            print(f"SURVIVED  {sp['id']}: the tests fail, but not with "
                  f"{sp['expect']!r}\n{_tail(res)}")
        else:
            killed += 1
            print(f"KILLED    {sp['id']}")
    print(f"{killed}/{len(specs)} killed")
    return 0 if killed == len(specs) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="\n\n".join(__doc__.split("\n\n")[1:]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--keep", action="store_true", help="keep the scratch copy")
    ap.add_argument("--out", type=Path, help="also write the report here")
    args = ap.parse_args(argv)
    specs = json.loads(args.spec.read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="jxlphoto_mutate_"))
    try:
        _copy_repo(work)
        if args.out:
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run(specs, work)
            sys.stdout.write(buf.getvalue())
            args.out.write_text(buf.getvalue(), encoding="utf-8")
            return rc
        return run(specs, work)
    finally:
        if args.keep:
            print(f"copy kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
