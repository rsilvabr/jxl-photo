"""F8 proof: the wrapper's pre-run verdicts — how many files a run takes
(_count_origin_files) and which manifest rows collide (_manifest_output_collisions,
with its cross_sink) — OLD against NEW, on the same hostile trees.

    python tools/verdicts.py <old rev> [<new rev>|WORKTREE|<folder>]

Each version runs in its own process (verdicts_worker.py): the wrapper imports
the children by name, so two versions in one process would share them. Every
difference printed is either a bug of the old mirror (for the owner) or an
error of the new code."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
FILES = ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py",
         "jxl_recompressor.py", "jxl_photo.py", "jxl_photo_common.py")
OLD = sys.argv[1]
NEW = sys.argv[2] if len(sys.argv) > 2 else "WORKTREE"


def extract(rev, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        if rev == "WORKTREE":
            (dest / f).write_bytes((REPO / f).read_bytes())
        elif Path(rev).is_dir():
            (dest / f).write_bytes((Path(rev) / f).read_bytes())
        else:
            (dest / f).write_bytes(subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{f}"],
                                                  capture_output=True, check=True).stdout)


def touch(root, *names):
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def build(trees):
    """The trees: each a folder of dummy files with the shapes that broke the
    mirror before (nested markers, the tools' own output folders, excluded
    folders, page-shaped names, rename targets), all with hostile names."""
    h = "吾妻山 [FINAL] 100%off"
    t = trees / f"t1 flat {h}"
    touch(t, "DSC_0001 (2).tif", "DSC_0002.tif", "DSC_0002_page1.tif", "a.jxl", "b (1).jxl",
          "c.jpg", "d.jpeg", "e.png")
    t = trees / f"t2 marker {h}"
    touch(t, "2025_EXPORT/TIFF16 [a]/DSC_0001.tif", "2025_EXPORT/TIFF16 [a]/DSC_0002.tif",
          "2025_EXPORT/16B_JXL/DSC_0001.jxl", "2025_EXPORT/JPG/DSC_0001.jpg",
          "2025_EXPORT/16B_JXL_small/DSC_0001.jxl", "2025_EXPORT/converted_jxl/c.jxl",
          "2025_EXPORT/sRGB_JXL/s.jxl", "loose/DSC_0009.tif", "loose/x.jxl")
    t = trees / f"t3 nested {h}"
    touch(t, "X_EXPORT/X_EXPORT/TIFF16/p.tif", "X_EXPORT/TIFF16/p.tif",
          "X_EXPORT/TIFF16/OLD_TIFFs/old.tif", "X_EXPORT/JXL/p.jxl", "X_EXPORT/JXL/OLD_TIFFs/q.jxl",
          "X_EXPORT/16B_JXL/p.jxl", "X_EXPORT/16B_JXL/p_page1.jxl")
    t = trees / f"t4 rename {h}"
    touch(t, "sub A/IMG_ProPhoto.tif", "sub B/IMG_sRGB.tif", "sub A/IMG_ProPhoto.jxl",
          "sub B/IMG_sRGB.jxl", "sub A/IMG_ProPhoto.jpg", "sub B/IMG_sRGB.jpg")
    manifests = {}
    for origin, dest, mode in (("tiff", "jxl", 2), ("tiff", "jxl", 6), ("tiff", "jxl", 7),
                               ("tiff", "jxl", 0), ("jxl", "tiff", 2), ("jxl", "tiff", 6),
                               ("jxl", "jxl", 2), ("jxl", "jxl", 6), ("jxl", "jxl", 1),
                               ("jpeg", "jxl", 2), ("jxl", "jpeg", 2), ("jxl", "jpeg", 6)):
        for tree in ("t2 marker", "t3 nested", "t4 rename"):
            tname = f"{tree} {h}"
            rows = [[tname, "", mode], [tname + "/loose" if tree == "t2 marker" else tname, "", mode]]
            if mode in (0, 1, 2):
                rows = [[tname, f"{tname}/out", mode], [tname, f"{tname}/out", mode]]
                if tree == "t4 rename":
                    rows = [[f"{tname}/sub A", f"{tname}/out", mode],
                            [f"{tname}/sub B", f"{tname}/out", mode]]
            manifests[f"{tree} {origin}->{dest} mode {mode}"] = {
                "origin": origin, "dest": dest, "rows": rows,
                "subfolder": "TIFF16" if mode == 7 else None}
            if tree == "t4 rename" and mode == 2:
                manifests[f"{tree} {origin}->{dest} mode {mode} rename"] = {
                    "origin": origin, "dest": dest, "rows": rows,
                    "rename_from": "ProPhoto", "rename_to": "sRGB"}
    (trees / "manifests.json").write_text(json.dumps(manifests, ensure_ascii=False, indent=1),
                                          encoding="utf-8")


base = Path(tempfile.mkdtemp(prefix="f8_verdicts_"))
trees = base / "trees 吾妻山 [x]"
trees.mkdir()
build(trees)
outs = {}
for label, rev in (("old", OLD), ("new", NEW)):
    code = base / f"code_{label}"
    extract(rev, code)
    out = base / f"{label}.json"
    r = subprocess.run([sys.executable, str(HERE / "verdicts_worker.py"), str(code), str(trees),
                        str(out)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise SystemExit(f"{label} worker failed")
    outs[label] = json.loads(out.read_text(encoding="utf-8"))
diff = 0
total = 0
for kind in ("counts", "collisions"):
    for key in sorted(set(outs["old"][kind]) | set(outs["new"][kind])):
        total += 1
        a, b = outs["old"][kind].get(key), outs["new"][kind].get(key)
        if a != b:
            diff += 1
            print(f"DIFF {kind} [{key}]\n    old {json.dumps(a, ensure_ascii=False)[:500]}\n"
                  f"    new {json.dumps(b, ensure_ascii=False)[:500]}")
print(f"\n{total - diff}/{total} verdicts identical ({OLD} -> {NEW})")
shutil.rmtree(base, ignore_errors=True)
sys.exit(1 if diff else 0)
