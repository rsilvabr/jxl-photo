"""F7 proof: every planning-time batch reader, OLD (a git revision) against
NEW (another revision, or the working tree with WORKTREE), fed the SAME fake
exiftool answers, must return the same value and log the same lines.

    python tools/batch_equivalence.py <old rev> [<new rev>|WORKTREE]

The fake replaces each loaded script's _run_captured: it reads the argfile to
learn which paths were asked and answers per scenario. No exiftool runs."""
import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILES = ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py",
         "jxl_recompressor.py", "jxl_photo.py", "jxl_photo_common.py")
OLD = sys.argv[1]
NEW = sys.argv[2] if len(sys.argv) > 2 else "WORKTREE"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["JXLPHOTO_SETTINGS"] = ""
os.environ.setdefault("JXLPHOTO_LOG_DIR", tempfile.mkdtemp(prefix="f7_logs_"))


def extract(rev, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        if rev == "WORKTREE":
            (dest / f).write_bytes((REPO / f).read_bytes())
        elif Path(rev).is_dir():          # a folder of the six files (a planted copy)
            (dest / f).write_bytes((Path(rev) / f).read_bytes())
        else:
            (dest / f).write_bytes(subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{f}"],
                                                  capture_output=True, check=True).stdout)


def load(folder, name, tag):
    spec = importlib.util.spec_from_file_location(f"{name}__{tag}", folder / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


TOKENS = ["jxlphoto-src:abc123", "jxlphoto-srcsum:def456", "jxlphoto-derived:sRGB@long2048",
          "jxlphoto-mpg:g1", "jxlphoto-pages:3", "jxlphoto-page:1", "jxlphoto-thumb",
          "jxlphoto-pages:x", "user value, kept"]


def entry(path, scenario, i):
    e = {"SourceFile": path, "Relation": TOKENS if i % 2 == 0 else TOKENS[3:5],
         "ImageWidth": 160, "ImageHeight": 120,
         "Description": "gen=2 | cjxl d=0.1 e=7 | cjxl d=1 e=7", "Software": "app",
         "HistoryParameters": ["converted | gen=1 | cjxl d=0.1 e=7"],
         "CreatorTool": "Capture One | ICC:QUJD"}
    if scenario == "case_swapped":
        e["SourceFile"] = path[0].lower() + path[1:].replace("\\", "/")
    if scenario == "error_entry" and i == 1:
        e = {"SourceFile": path, "Error": "File not found"}
    if i == 2 and scenario == "no_relation":
        e.pop("Relation")
    return e


def make_fake(scenario, calls):
    def fake(cmd, timeout, text=False, input=None, stall=None):
        argfile = Path(cmd[2])
        lines = argfile.read_text(encoding="utf-8").splitlines()
        paths = [ln for ln in lines if ln and not ln.startswith("-")
                 and ln not in ("FileName=UTF8", "UTF8", "utf8")]
        calls.append(len(paths))
        if scenario == "raise_timeout":
            # a fixed command: the real one names a random temp argfile
            raise subprocess.TimeoutExpired(["exiftool", "-@", "<argfile>"], timeout)
        if scenario == "empty_rc1" or (scenario == "second_chunk_fails" and len(calls) == 2):
            return subprocess.CompletedProcess(cmd, 1, "", "Error: something")
        data = [entry(p, scenario, i) for i, p in enumerate(paths)]
        if scenario == "unknown_path":
            data.append(entry("Z:\\never asked.jxl", scenario, 0))
        out = json.dumps(data, ensure_ascii=False)
        if scenario == "half_json":
            out = out[:len(out) // 2]
        rc = 1 if scenario in ("rc1_with_json", "error_entry") else 0
        return subprocess.CompletedProcess(cmd, rc, out, "")
    return fake


SCENARIOS = ["ok", "empty_rc1", "half_json", "error_entry", "rc1_with_json", "raise_timeout",
             "case_swapped", "unknown_path", "no_relation", "second_chunk_fails"]
READERS = [
    ("jxl_tiff_encoder", "_read_source_markers_batch"),
    ("jxl_jpeg_transcoder", "_read_derived_markers_batch"),
    ("jxl_tiff_encoder", "_read_page_markers_batch"),
    ("jxl_tiff_encoder", "_read_group_markers_batch"),
    ("jxl_tiff_decoder", "_xmp_icc_jxls_batch"),
    ("jxl_tiff_decoder", "_read_multipage_markers_batch"),
    ("jxl_jpeg_transcoder", "_read_thumb_markers_batch"),
    ("jxl_recompressor", "_read_encode_params_batch"),
    ("jxl_recompressor", "_read_mpg_markers"),
    ("jxl_recompressor", "_read_mpg_pages_batch"),
]


def normal(v):
    if isinstance(v, set):
        return sorted(normal(x) for x in v)
    if isinstance(v, dict):
        return {str(k): normal(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [normal(x) for x in v]
    if isinstance(v, Path):
        return str(v)
    return v


def run_reader(mod, fn, scenario):
    calls = []
    mod._run_captured = make_fake(scenario, calls)
    log = io.StringIO()
    h = logging.StreamHandler(log)
    h.setLevel(logging.DEBUG)
    h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    lg = mod.logger
    lg.addHandler(h)
    old_level = lg.level
    lg.setLevel(logging.DEBUG)
    try:
        n = 401 if scenario == "second_chunk_fails" else 3
        root = Path("E:\\photos\\250118_吾妻山 [FINAL] 5%")
        paths = [root / f"DSC_{i:04d} (2).jxl" for i in range(n)]
        try:
            value = normal(getattr(mod, fn)(paths))
        except Exception as e:      # an exception is part of the behaviour
            value = f"RAISED {type(e).__name__}: {e}"
    finally:
        lg.removeHandler(h)
        lg.setLevel(old_level)
    return value, log.getvalue().splitlines(), calls


base = Path(tempfile.mkdtemp(prefix="f7_equiv_"))
old_dir, new_dir = base / "old", base / "new"
extract(OLD, old_dir)
extract(NEW, new_dir)
sys.path.insert(0, str(new_dir))
mods = {}
for script in sorted({s for s, _ in READERS}):
    mods[(script, "old")] = load(old_dir, script, "old")
    mods[(script, "new")] = load(new_dir, script, "new")

bad = 0
for script, fn in READERS:
    for scenario in SCENARIOS:
        a = run_reader(mods[(script, "old")], fn, scenario)
        b = run_reader(mods[(script, "new")], fn, scenario)
        same = a == b
        bad += not same
        print(f"{'SAME' if same else 'DIFF'}  {script}.{fn}  [{scenario}]")
        if not same:
            for label, x, y in (("value", a[0], b[0]), ("log", a[1], b[1]), ("calls", a[2], b[2])):
                if x != y:
                    print(f"      {label}:\n        old {x!r:.600}\n        new {y!r:.600}")
print(f"\n{len(READERS) * len(SCENARIOS) - bad}/{len(READERS) * len(SCENARIOS)} identical "
      f"({OLD} -> {NEW})")
sys.exit(1 if bad else 0)
