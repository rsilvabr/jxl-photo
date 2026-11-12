#!/usr/bin/env python3
"""Wizard transcripts: the same scripted sessions with two versions, compared.

    python tools/wizard_transcripts.py --ref <rev> [--new WORKTREE|<rev>|<folder>]
           [--ui rich|plain|both] [--expect FILE] [--work DIR] [--keep] [SESSION ...]

The sessions are tools/ui_tour.py's: a fixed sequence of keys on stdin, the
way a person answers -- so the real PlainUI/RichUI input parsing runs, not a
ScriptedUI. Each session runs once with the REFERENCE's six files and once
with the NEW ones, in the SAME folders (the app folder, a fresh home with a
seeded last job and preset, an empty library whose name has Japanese, [ ], a
space and %), and three things are compared:

- everything the wizard printed (Rich at 110 columns, or plain text);
- every command line it handed to a child script. The child is NOT run: a
  small bootstrap replaces the wrapper's _stream_child with a recorder that
  answers "exit 0", so a session can reach the post-run screens too;
- the wrapper's config file (~/.jxl_tools_config.json) at the end.

Masked: clock times, dates and log file names. --expect lists regular
expressions for differences a change means to make (reported as EXPECTED).
Exit status: 0 when nothing unexpected differs, 1 otherwise.

Prove it before trusting it: the reference against itself gives zero, and a
question moved or reworded in a copy (--new <folder>) is reported.
"""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIX = ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py",
       "jxl_recompressor.py", "jxl_photo.py", "jxl_photo_common.py")
LIB_NAME = "250118_吾妻山公園 [FINAL] 5%"

_BOOTSTRAP = r'''
import json, sys
from pathlib import Path
app, record, ui = sys.argv[1], sys.argv[2], sys.argv[3]
if ui == "plain":
    sys.modules["rich"] = None          # the wrapper's `import rich` fails: PlainUI
sys.path.insert(0, app)
import jxl_photo as wp

def _recorded(self, cmd, idle_timeout=None):
    with open(record, "a", encoding="utf-8") as f:
        f.write(json.dumps([str(c) for c in cmd], ensure_ascii=False) + "\n")
    print(f"[transcript] child not run: {Path(str(cmd[1])).name if len(cmd) > 1 else cmd}")
    return 0

for _obj in list(vars(wp).values()):
    if isinstance(_obj, type) and "_stream_child" in vars(_obj):
        _obj._stream_child = _recorded
sys.argv = [str(Path(app) / "jxl_photo.py")]
sys.exit(wp.main())
'''

_MASKS = [
    (re.compile(r"\b\d{8}_\d{6}(_\d+)*\.log\b"), "<log>"),
    (re.compile(r"\b\d{4}-\d\d-\d\d([ T]\d\d:\d\d(:\d\d)?)?\b"), "<date>"),
    (re.compile(r"\b\d\d:\d\d(:\d\d)?\b"), "<clock>"),
    (re.compile(r"\b\d+(\.\d+)?\s?(ms|s|sec|seconds|min)\b"), "<t>"),
]


def _ui_tour():
    spec = importlib.util.spec_from_file_location("ui_tour_sessions", REPO / "tools" / "ui_tour.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _materialize(spec: str, dest: Path) -> str:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if spec == "WORKTREE":
        for f in SIX:
            shutil.copy2(REPO / f, dest / f)
        return "working tree"
    if Path(spec).is_dir():
        for f in SIX:
            shutil.copy2(Path(spec) / f, dest / f)
        return str(Path(spec))
    for f in SIX:
        r = subprocess.run(["git", "-C", str(REPO), "show", f"{spec}:{f}"], capture_output=True)
        if r.returncode != 0:
            raise SystemExit(f"cannot read {f} at {spec}")
        (dest / f).write_bytes(r.stdout)
    rev = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", spec],
                         capture_output=True, text=True).stdout.strip()
    return f"{spec} ({rev})"


def _normalize(text: str) -> list:
    out = []
    for line in text.splitlines():
        line = line.rstrip()
        for rx, token in _MASKS:
            line = rx.sub(token, line)
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return out


def _mask_json(value):
    if isinstance(value, dict):
        return {k: _mask_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_json(v) for v in value]
    if isinstance(value, str):
        for rx, token in _MASKS:
            value = rx.sub(token, value)
    return value


def run_session(tour, code: Path, work: Path, name: str, ui: str) -> dict:
    app, home, logs = work / "app", work / "home", work / "logs"
    lib = work / "lib" / LIB_NAME
    for p in (app, home, logs, work / "lib"):
        if p.exists():
            shutil.rmtree(p)
    for p in (home, logs, app / "manifests", lib / "01_Z7" / "_EXPORT" / "TIFF16"):
        p.mkdir(parents=True, exist_ok=True)
    for f in SIX:
        shutil.copy2(code / f, app / f)
    env = dict(os.environ, USERPROFILE=str(home), HOME=str(home), JXLPHOTO_LOG_DIR=str(logs),
               JXLPHOTO_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8", COLUMNS="110",
               JXLPHOTO_JXL_THUMB="0", PYTHONDONTWRITEBYTECODE="1")
    env.pop("JXLPHOTO_SETTINGS", None)
    tour._seed_config(app, home, lib, env)
    keys = tour._sessions(lib)[name]
    record = work / "argv.jsonl"
    if record.exists():
        record.unlink()
    boot = work / "bootstrap.py"
    boot.write_text(_BOOTSTRAP, encoding="utf-8")
    r = subprocess.run([sys.executable, "-B", str(boot), str(app), str(record), ui], input=keys,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, cwd=str(app), timeout=300)
    argv = []
    if record.exists():
        argv = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines() if line]
    cfg_path = home / ".jxl_tools_config.json"
    config = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else None
    text = r.stdout + ("\n--- stderr ---\n" + r.stderr if r.stderr.strip() else "")
    return {"rc": r.returncode, "transcript": _normalize(text),
            "argv": _mask_json(argv), "config": _mask_json(config)}


def _dict_diff(prefix: str, a, b) -> list:
    if a == b:
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _dict_diff(f"{prefix}.{k}", a.get(k), b.get(k))
        return out
    return [f"{prefix}: {json.dumps(a, ensure_ascii=False)[:200]} -> {json.dumps(b, ensure_ascii=False)[:200]}"]


def compare(label: str, ref: dict, new: dict) -> list:
    out = []
    if ref["rc"] != new["rc"]:
        out.append(f"{label} | rc: {ref['rc']} -> {new['rc']}")
    sm = difflib.SequenceMatcher(None, ref["transcript"], new["transcript"], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("replace", "delete"):
            out += [f"{label} | screen -: {x}" for x in ref["transcript"][i1:i2]]
        if op in ("replace", "insert"):
            out += [f"{label} | screen +: {x}" for x in new["transcript"][j1:j2]]
    if ref["argv"] != new["argv"]:
        for k in range(max(len(ref["argv"]), len(new["argv"]))):
            a = ref["argv"][k] if k < len(ref["argv"]) else None
            b = new["argv"][k] if k < len(new["argv"]) else None
            if a != b:
                out.append(f"{label} | argv {k + 1}: {json.dumps(a, ensure_ascii=False)} -> "
                           f"{json.dumps(b, ensure_ascii=False)}")
    out += [f"{label} | {d}" for d in _dict_diff("config", ref["config"], new["config"])]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="\n\n".join(__doc__.split("\n\n")[1:]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--new", default="WORKTREE")
    ap.add_argument("--ui", choices=("rich", "plain", "both"), default="both")
    ap.add_argument("--expect", type=Path)
    ap.add_argument("--work", type=Path, help="scratch folder (default: %%TEMP%%\\jxlphoto_transcripts)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch folder and the transcripts")
    ap.add_argument("--report", type=Path)
    ap.add_argument("sessions", nargs="*", help="only these ui_tour sessions (default: all)")
    args = ap.parse_args(argv)

    tour = _ui_tour()
    all_sessions = list(tour._sessions(Path("x")))
    unknown = [s for s in args.sessions if s not in all_sessions]
    if unknown:
        print(f"unknown session(s): {', '.join(unknown)}; known: {', '.join(all_sessions)}")
        return 2
    names = args.sessions or all_sessions
    uis = ["rich", "plain"] if args.ui == "both" else [args.ui]
    expect = []
    if args.expect:
        expect = [re.compile(x.strip()) for x in args.expect.read_text(encoding="utf-8").splitlines()
                  if x.strip() and not x.lstrip().startswith("#")]
    work = args.work or Path(os.environ.get("TEMP", "/tmp")) / "jxlphoto_transcripts"
    work.mkdir(parents=True, exist_ok=True)
    ref_label = _materialize(args.ref, work / "code_ref")
    new_label = _materialize(args.new, work / "code_new")
    lines = [f"# Wizard transcripts — {time.strftime('%Y-%m-%d %H:%M')}", "",
             f"- ref: {ref_label}", f"- new: {new_label}", f"- screens: {', '.join(uis)}", ""]
    unexpected = expected_n = 0
    t0 = time.time()
    for ui in uis:
        for name in names:
            ref = run_session(tour, work / "code_ref", work, name, ui)
            new = run_session(tour, work / "code_new", work, name, ui)
            label = f"{name} [{ui}]"
            diffs = compare(label, ref, new)
            exp = [d for d in diffs if any(rx.search(d) for rx in expect)]
            unexp = [d for d in diffs if d not in exp]
            unexpected += len(unexp)
            expected_n += len(exp)
            state = "DIFFERENT" if unexp else ("EXPECTED" if exp else "SAME")
            lines.append(f"{state:<9} {label:32} rc {ref['rc']:>3}  {len(ref['transcript']):>4} lines"
                         f"  {len(ref['argv'])} argv")
            print(lines[-1], flush=True)
            for d in unexp[:30]:
                lines.append(f"    ! {d}")
                print(lines[-1], flush=True)
            for d in exp[:10]:
                lines.append(f"    ~ {d}")
            if args.keep:
                out = work / "transcripts" / ui
                out.mkdir(parents=True, exist_ok=True)
                for side, res in (("ref", ref), ("new", new)):
                    (out / f"{name}.{side}.txt").write_text("\n".join(res["transcript"]) + "\n",
                                                           encoding="utf-8")
    lines += ["", f"{unexpected} unexpected difference(s), {expected_n} expected, {time.time() - t0:.0f} s"]
    print(lines[-1])
    report = args.report or REPO / "AI_tools" / f"{time.strftime('%y%m%d')}_wizard_transcripts_report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {report}")
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
