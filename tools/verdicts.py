"""F8 proof: the wrapper's pre-run verdicts — how many files a run takes
(_count_origin_files) and which manifest rows collide (_manifest_output_collisions,
with its cross_sink) — OLD against NEW, on the same hostile trees.

    python tools/verdicts.py <old rev> [<new rev>|WORKTREE|<folder>] [--keep]

Each version runs in its own process (verdicts_worker.py): the wrapper imports
the children by name, so two versions in one process would share them. Every
difference printed is either a bug of the old mirror (for the owner) or an
error of the new code.

The trees hold the shapes that broke the mirror before, all under hostile
names (Japanese, [ ], a space, `100%off %d`): nested markers, a marker-like
folder ABOVE the row's Source, the scripts' own output folders, excluded
folders, page-shaped names, rename targets, JXL containers with and without a
jbrd box (the bit-exact recovery keeps the original name), single-file rows
(Destination = the file, as the manifest loader fills an empty cell, and an
explicit folder) and Destination = Source rows in modes 3-8 (the loader's
default). Every conversion type of the transcoder is asked, plus one the
wrapper does not know (the child then runs in auto mode).
--keep leaves the scratch folder (trees, both JSON answers) for a look."""
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
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
KEEP = "--keep" in sys.argv[1:]
OLD = _args[0]
NEW = _args[1] if len(_args) > 1 else "WORKTREE"

# (origin, dest, conversion type): the wrapper's directions with every
# conversion type it has, and one it does not know ("unknown_type": no
# conversion flag reaches the transcoder, which then runs in auto mode).
PLANS = [("tiff", "jxl", "jxl_tiff_encoder"), ("jxl", "tiff", "jxl_tiff_decoder"),
         ("jxl", "jxl", "jxl_recompress"),
         ("jpeg", "jxl", "transcode_lossless"), ("jpeg", "jxl", "convert_lossy"),
         ("jpeg", "jxl", "unknown_type"),
         ("jxl", "jpeg", "jxl_to_jpeg_auto"), ("jxl", "jpeg", "jxl_to_jpeg_lossless"),
         ("jxl", "jpeg", "jxl_to_jpeg_force"), ("jxl", "png", "jxl_to_png")]

# A JXL container (signature box, ftyp, a 2-byte codestream) with or without a
# jbrd box: what has_jbrd_box reads. Nothing here is a decodable image.
_SIG = b"\x00\x00\x00\x0cJXL \r\n\x87\n"
_FTYP = b"\x00\x00\x00\x14ftypjxl \x00\x00\x00\x00jxl "
_JXLC = b"\x00\x00\x00\x0ajxlc\xff\x0a"
JBRD = _SIG + _FTYP + b"\x00\x00\x00\x08jbrd" + _JXLC
PLAIN_JXL = _SIG + _FTYP + _JXLC


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


def touch(root, *names, content=b"x"):
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def build(trees):
    """The trees (see the module docstring), the manifests and the count inputs."""
    h = "吾妻山 [FINAL] 100%off %d"
    t = trees / f"t1 flat {h}"
    touch(t, "DSC_0001 (2).tif", "DSC_0002.tif", "DSC_0002_page1.tif", "a.jxl", "b (1).jxl",
          "c.jpg", "d.jpeg", "e.png", "f.jfif")
    touch(t, "j1 [jbrd].jxl", content=JBRD)
    touch(t, "j2 plain.jxl", content=PLAIN_JXL)
    touch(t, "j1 [jbrd].jpg")
    t = trees / f"t2 marker {h}"
    touch(t, "2025_EXPORT/TIFF16 [a]/DSC_0001.tif", "2025_EXPORT/TIFF16 [a]/DSC_0002.tif",
          "2025_EXPORT/16B_JXL/DSC_0001.jxl", "2025_EXPORT/JPG/DSC_0001.jpg",
          "2025_EXPORT/16B_JXL_small/DSC_0001.jxl", "2025_EXPORT/converted_jxl/c.jxl",
          "2025_EXPORT/sRGB_JXL/s.jxl", "loose/DSC_0009.tif", "loose/x.jxl",
          "2025_EXPORT/16B_TIFF/DSC_0001.tif", "2025_EXPORT/recovered_jpeg/r.jpg",
          "2025_EXPORT/converted/k.jpg", "2025_EXPORT/recompressed_jxl/DSC_0001.jxl",
          "2025_EXPORT/JXL_small/DSC_0002.jxl", "2025_EXPORT/PNG/DSC_0001.png",
          "2025_EXPORT/OLD_TIFFs/DSC_0001.tif", "2025_EXPORT/OLD_TIFFs/DSC_0001.jxl")
    touch(t, "2025_EXPORT/JXL_jpeg/DSC_0001.jxl", content=JBRD)
    t = trees / f"t3 nested {h}"
    touch(t, "X_EXPORT/X_EXPORT/TIFF16/p.tif", "X_EXPORT/TIFF16/p.tif",
          "X_EXPORT/TIFF16/OLD_TIFFs/old.tif", "X_EXPORT/JXL/p.jxl", "X_EXPORT/JXL/OLD_TIFFs/q.jxl",
          "X_EXPORT/16B_JXL/p.jxl", "X_EXPORT/16B_JXL/p_page1.jxl",
          "X_EXPORT/X_EXPORT/JPG/p.jpg", "X_EXPORT/JPG/p.jpg")
    for name, a_content in (("t4 rename jbrd", JBRD), ("t5 rename plain", PLAIN_JXL)):
        t = trees / f"{name} {h}"
        touch(t, "sub A/IMG_ProPhoto.tif", "sub B/IMG_sRGB.tif",
              "sub A/IMG_ProPhoto.jpg", "sub B/IMG_sRGB.jpg")
        touch(t, "sub A/IMG_ProPhoto.jxl", content=a_content)
        touch(t, "sub B/IMG_sRGB.jxl", content=PLAIN_JXL)
    t = trees / f"t6 above {h}"
    touch(t, "Lightroom_Export/lib/2025_EXPORT/TIFF16/p.tif",
          "Lightroom_Export/lib/2025_EXPORT/16B_JXL/p.jxl",
          "Lightroom_Export/lib/2025_EXPORT/JPG/p.jpg",
          "Lightroom_Export/lib/loose/q.tif", "Lightroom_Export/lib/loose/q.jxl",
          "Lightroom_Export/lib/loose/q.jpg", "Lightroom_Export/other/p.tif",
          "Lightroom_Export/other/p.jxl", "Lightroom_Export/other/p.jpg")
    t = trees / f"t7 siblings {h}"
    touch(t, "sub1/a.tif", "sub2/a.tif", "sub1/a.jxl", "sub2/a.jxl", "sub1/a.jpg", "sub2/a.jpg",
          "sub1_TIFF/b.tif", "sub2_TIFF/b.tif", "sub1_JXL/b.jxl", "sub2_JXL/b.jxl",
          "sub1_JPG/b.jpg", "sub2_JPG/b.jpg")

    manifests = {}

    def add(key, plan, rows, **kw):
        origin, dest, conv = plan
        manifests[f"{key} | {origin}->{dest} {conv}"] = dict(
            origin=origin, dest=dest, conv=conv, rows=rows, **kw)

    for plan in PLANS:
        for mode in range(9):
            sub = "TIFF16" if mode == 7 else None
            for tree in ("t1 flat", "t2 marker", "t3 nested", "t6 above", "t7 siblings"):
                tn = f"{tree} {h}"
                if mode in (0, 1, 2):
                    rows = [[tn, f"{tn}/out", mode], [tn, f"{tn}/out", mode]]
                    if tree == "t7 siblings":
                        rows = [[f"{tn}/sub1", f"{tn}/out", mode],
                                [f"{tn}/sub2", f"{tn}/out", mode]]
                else:
                    # Destination = Source: what the manifest loader puts in an
                    # empty cell.
                    second = {"t2 marker": f"{tn}/loose",
                              "t6 above": f"{tn}/Lightroom_Export/other",
                              "t7 siblings": f"{tn}/sub2"}.get(tree, tn)
                    first = {"t6 above": f"{tn}/Lightroom_Export/lib",
                             "t7 siblings": f"{tn}/sub1"}.get(tree, tn)
                    rows = [[first, first, mode], [second, second, mode]]
                add(f"{tree} mode {mode}", plan, rows, subfolder=sub)
            if mode in (6, 7):
                # An explicit Destination in a marker mode (ignored for the
                # folder; the marker anchor is the children's to say).
                tn = f"t6 above {h}"
                add(f"t6 above mode {mode} explicit destination", plan,
                    [[f"{tn}/Lightroom_Export/lib", f"{tn}/out", mode],
                     [f"{tn}/Lightroom_Export/other", f"{tn}/out", mode]], subfolder=sub)
                tn = f"t2 marker {h}"
                add(f"t2 marker mode {mode} custom folder", plan,
                    [[tn, tn, mode], [f"{tn}/loose", f"{tn}/loose", mode]],
                    subfolder=sub, export_jxl_folder="16B_JXL_custom")
            for tree in ("t4 rename jbrd", "t5 rename plain"):
                tn = f"{tree} {h}"
                if mode in (0, 1, 2):
                    rows = [[f"{tn}/sub A", f"{tn}/out", mode],
                            [f"{tn}/sub B", f"{tn}/out", mode]]
                else:
                    rows = [[f"{tn}/sub A", f"{tn}/sub A", mode],
                            [f"{tn}/sub B", f"{tn}/sub B", mode]]
                add(f"{tree} mode {mode}", plan, rows, subfolder=sub)
                add(f"{tree} mode {mode} rename", plan, rows, subfolder=sub,
                    rename_from="ProPhoto", rename_to="sRGB")
            # Single-file rows: the file as its own Destination (an empty
            # cell, as the loader fills it) and an explicit folder, next to a
            # folder row writing into that same folder.
            tn = f"t1 flat {h}"
            tn7 = f"t7 siblings {h}"
            for fname in ("DSC_0002.tif", "a.jxl", "c.jpg", "j1 [jbrd].jxl", "j2 plain.jxl",
                          "e.png"):
                f = f"{tn}/{fname}"
                add(f"file {fname} mode {mode} dest=file", plan,
                    [[f, f, mode], [tn, tn, mode]], subfolder=sub)
                add(f"file {fname} mode {mode} dest=folder", plan,
                    [[f, f"{tn}/out", mode], [tn, f"{tn}/out", mode]], subfolder=sub)
            for fname in ("sub1/a.tif", "sub1/a.jxl", "sub1/a.jpg"):
                f = f"{tn7}/{fname}"
                add(f"file t7 {fname} mode {mode} vs sub2", plan,
                    [[f, f, mode], [f"{tn7}/sub2", f"{tn7}/sub2", mode]], subfolder=sub)
                add(f"file t7 {fname} mode {mode} rename", plan,
                    [[f, f"{tn7}/out", mode], [f"{tn7}/sub2", f"{tn7}/out", mode]],
                    subfolder=sub, rename_from="a", rename_to="b")
    (trees / "manifests.json").write_text(json.dumps(manifests, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    inputs = [p.name for p in sorted(trees.iterdir()) if p.is_dir()]
    t1 = f"t1 flat {h}"
    inputs += [f"{t1}/{n}" for n in ("DSC_0002.tif", "a.jxl", "c.jpg", "j1 [jbrd].jxl",
                                     "j2 plain.jxl", "e.png", "f.jfif")]
    inputs += [f"t2 marker {h}/2025_EXPORT/16B_JXL/DSC_0001.jxl",
               f"t2 marker {h}/2025_EXPORT/TIFF16 [a]/DSC_0001.tif",
               f"t2 marker {h}/2025_EXPORT/JPG/DSC_0001.jpg"]
    (trees / "count_inputs.json").write_text(json.dumps(inputs, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    return len(manifests), len(inputs)


base = Path(tempfile.mkdtemp(prefix="f8_verdicts_"))
trees = base / "trees 吾妻山 [x] 100%off %d"
trees.mkdir()
n_man, n_in = build(trees)
print(f"{n_man} manifests, {n_in} count inputs, in {trees}")
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
    print(f"{label}: {r.stdout.strip()}")
    outs[label] = json.loads(out.read_text(encoding="utf-8"))
diff = 0
total = 0
for kind in ("counts", "collisions"):
    for key in sorted(set(outs["old"][kind]) | set(outs["new"][kind])):
        total += 1
        a, b = outs["old"][kind].get(key), outs["new"][kind].get(key)
        if a != b:
            diff += 1
            print(f"DIFF {kind} [{key}]\n    old {json.dumps(a, ensure_ascii=False)[:600]}\n"
                  f"    new {json.dumps(b, ensure_ascii=False)[:600]}")
print(f"\n{total - diff}/{total} verdicts identical ({OLD} -> {NEW})")
if KEEP:
    print(f"kept: {base}")
else:
    shutil.rmtree(base, ignore_errors=True)
sys.exit(1 if diff else 0)
