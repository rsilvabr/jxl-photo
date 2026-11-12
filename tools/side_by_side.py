#!/usr/bin/env python3
"""Side by side: the same runs with two versions of the scripts, compared.

    python tools/side_by_side.py --ref <rev> [--new WORKTREE|<rev>|<folder>]
           [--fixtures E:\\TESTE] [--quick] [--only NAME ...] [--lane w1|default|both]
           [--expect FILE] [--work DIR] [--keep] [--report FILE]

A refactor must not change what the toolkit does. This runs every scenario
twice -- once with the six files of the REFERENCE (a git revision), once with
the NEW ones (the working tree, another revision, or a folder holding the six
files) -- in the SAME folder, restored from a pristine copy in between, and
compares everything a user could observe:

- the exit code of every step and its `##JXLSUM##` summary;
- the console output and the log file, line by line (`--lane w1`, one worker,
  in order) or as a sorted list without the `[n/total]` counters
  (`--lane default`, the run's own worker count);
- after every step, the whole folder: which files exist; for each JXL the
  codestream (sha256), the boxes (type, size, order) and every metadata tag
  exiftool reads (-a -G1 -struct); for each TIFF/JPEG/PNG the decoded pixels
  and the metadata; `checksums.md5` (the names it lists, and that each line
  matches its file); any other file by its bytes.

Only what changes from one run to the next with the SAME code is masked: the
History `When`, the file system dates, clock times, durations, the log
file's name, temp names, the free-memory line. Running both sides in the same
folder keeps `jxlphoto-src` (a hash of the source's path) comparable.

Every scenario runs inside a hostile folder (Japanese, [ ], a space and
`100%off %d`; a few run in one without `%` too) on the fixtures' drive.
The fixtures are copied, never modified. `--expect FILE` lists regular
expressions (one per line, # for comments) for differences a phase means to
make; a difference matching one is reported as EXPECTED. Exit status: 0 when
no unexpected difference was found, 1 otherwise, 2 when nothing could run.

Prove the tool before trusting it: the reference against itself must give
zero differences (twice), and a change planted in a copy (`--new <folder>`)
must be reported.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
SIX = ("jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py",
       "jxl_recompressor.py", "jxl_photo.py", "jxl_photo_common.py")
SCRIPT = {"enc": "jxl_tiff_encoder.py", "dec": "jxl_tiff_decoder.py",
          "tra": "jxl_jpeg_transcoder.py", "rec": "jxl_recompressor.py"}
HOSTILE_PCT = "250118_吾妻山 [SBS] 100%off %d"
HOSTILE_PLAIN = "250118_吾妻山 [SBS] plain"
IMAGE_EXT = {".tif", ".tiff", ".jpg", ".jpeg", ".png"}
STEP_TIMEOUT = 1800


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

@dataclass
class Step:
    script: Optional[str]                      # enc/dec/tra/rec; None = a file action
    args: List[str] = field(default_factory=list)
    action: Optional[str] = None               # "touch:<rel>" (mtime + 10 s)
    stdin: Optional[str] = None                # text for stdin; default: closed


@dataclass
class Scenario:
    name: str
    files: Dict[str, str]                      # {path in the run folder: fixture path}
    steps: List[Step]
    quick: bool = False
    pct: bool = True                           # under the `100%off %d` folder
    writes: bool = True                        # False: a dry run / usage errors


T16 = {  # small 16-bit exports with ICC (a phone, a Z f, a WhatsApp JPEG, a 5 MP with IPTC)
    "a/IMG_20260809 (1).tif": "TIFF16_varied/Xiaomi17TPro_c1deflate_thumb_xpcomment__IMG_20260809_044623_ProPhoto-g22_v1.tif",
    "a/DSC_8320 [Zf].tif": "TIFF16_varied/NIKONZf_c1deflate_thumb_xpcomment__DSC_8320_ProPhoto-g22_v1.tif",
    "a/WhatsApp Image 2026-05-31.tif": "TIFF16_varied/JpegFile__WhatsApp Image 2026-05-31 at 23.54.04_ProPhoto-g22_v1.tif",
    "a/P1010028_iptc.tif": "TIFF16_varied/C5050Z_omworkspace_srgb_iptc__P1010028.tif",
}
SCANS = {  # 8-bit with a monitor ICC, no tags, gray 16 without ICC, a 2-page big-endian gray
    "s/DSC_36433645.tif": "TIFF_scans_varied/rgb8_silverfast_monitoricc_ifd13__DSC_36433645.tif",
    "s/ow foto!.tif": "TIFF_scans_varied/rgb8_lzw_notags__ow foto!.tif",
    "s/superpan0018.tif": "TIFF_scans_varied/gray16_noicc__superpan0018.tif",
    "s/0017 (2page).tif": "TIFF_scans_varied/gray8_silverfast_2page_be__0017.tif",
}
CAMTHUMB = {"c/DSC_0068.tif": "TIFF_camera_thumb/nxstudio_portrait_thumb107x160__DSC_0068_NXstudio.tif"}
JPEGS = {  # small JPEG structures, an empty file and a non-JPEG among them
    "j/wall14.jpg": "JPEG_varied/com_marker__wall14.jpg",
    "j/IMG-20161223-WA0070.jpg": "JPEG_varied/displayp3_v4_whatsapp__IMG-20161223-WA0070.jpg",
    "j/dabeat (444).jpg": "JPEG_varied/ducky_adobe_app14_444__dabeat.jpg",
    "j/balloon.JPG": "JPEG_varied/gray_progressive_com__balloon.JPG",
    "j/amarelo.jpg": "JPEG_varied/jfxx_thumbnail__amarelo.jpg",
    "j/imagejpeg_1.jpg": "JPEG_varied/motorola_c261_o0__imagejpeg_1.jpg",
    "j/DSC01367.JPG": "JPEG_varied/empty_file__DSC01367.JPG",
}
PNGS = {
    "p/Snow.png": "PNG_varied/palette8_gama_phys_time__Snow.png",
    "p/wall10.png": "PNG_varied/rgb8_chrm_gama_iccp__wall10.png",
    "p/IMG_5001.PNG": "PNG_varied/rgba8_plain__IMG_5001.PNG",
}
MASTERS = {
    "m/Zf_DSC_0025.jxl": "JXL_masters/Zf_DSC_0025_ProPhoto-g22_v1.jxl",
    "m/Zf_DSC_0016.jxl": "JXL_masters/Zf_DSC_0016_ProPhoto-g22_v1.jxl",
}


def _under(prefix: str, files: Dict[str, str]) -> Dict[str, str]:
    """The same fixtures under another folder of the run (layouts for modes 6/7)."""
    return {f"{prefix}/{Path(k).name}": v for k, v in files.items()}


def _scenarios() -> List[Scenario]:
    run, S = "{run}", Step
    exp = "ev 250118 [FINAL]/_EXPORT/TIFF16"
    sj = "--summary-json"
    return [
        # ---- encoder ----------------------------------------------------------
        Scenario("enc_m0_first", T16, [S("enc", [f"{run}/a", f"{run}/out", "--mode", "0", "--no-preflight", sj])], quick=True),
        Scenario("enc_m0_first_plain", T16, [S("enc", [f"{run}/a", f"{run}/out", "--mode", "0", "--no-preflight", sj])], pct=False),
        Scenario("enc_m0_rerun_sync", T16, [
            S("enc", [f"{run}/a", f"{run}/out", "--mode", "0", "--no-preflight", sj]),
            S("enc", [f"{run}/a", f"{run}/out", "--mode", "0", "--no-preflight", "--sync", sj])], quick=True),
        Scenario("enc_m0_touch_sync", T16, [
            S("enc", [f"{run}/a", f"{run}/out", "--mode", "0", "--no-preflight", sj]),
            S(None, action="touch:a/DSC_8320 [Zf].tif"),
            S("enc", [f"{run}/a", f"{run}/out", "--mode", "0", "--no-preflight", "--sync", sj])]),
        Scenario("enc_m2_tree", {**T16, **SCANS}, [S("enc", [run, f"{run}/flat", "--mode", "2", "--no-preflight", sj])]),
        # (a run folder's name must not END with "_export": the marker matches it)
        Scenario("enc_m7_subfolder", _under(exp, T16), [
            S("enc", [run, "--mode", "7", "--export-subfolder", "TIFF16", "--no-preflight", sj])], quick=True),
        Scenario("enc_m8_inplace", {**T16, **SCANS}, [S("enc", [run, "--mode", "8", "--no-preflight", sj])]),
        Scenario("enc_dry_run", _under(exp, T16), [
            S("enc", [run, "--mode", "7", "--export-subfolder", "TIFF16", "--dry-run", sj])],
                 quick=True, writes=False),
        Scenario("enc_delete", T16, [
            S("enc", [f"{run}/a", f"{run}/out", "--mode", "0", "--no-preflight", "--delete-source",
                      "--delete-confirm-off", sj])]),
        Scenario("enc_delete_skipped", T16, [
            S("enc", [f"{run}/a", f"{run}/out", "--mode", "0", "--no-preflight", sj]),
            S("enc", [f"{run}/a", f"{run}/out", "--mode", "0", "--no-preflight", "--sync", "--delete-source",
                      "--delete-skipped", "--delete-confirm-off", sj])]),
        Scenario("enc_scans_camthumb", {**SCANS, **CAMTHUMB}, [S("enc", [run, f"{run}/out", "--mode", "2", "--no-preflight", sj])]),
        Scenario("enc_usage_errors", {"a/x.tif": T16["a/IMG_20260809 (1).tif"]}, [
            S("enc", [f"{run}/a", f"{run}/out", "--distance", "30"]),
            S("enc", [f"{run}/a", f"{run}/out", "--mode", "9"]),
            S("enc", [f"{run}/a", f"{run}/out", "--no-overwrite-guard", "--delete-source"]),
            S("enc", [f"{run}/a", f"{run}/out", "--effort", "x"]),
            S("enc", [f"{run}/missing folder", f"{run}/out"]),
        ], quick=True, writes=False),
        Scenario("enc_long_path", {("d" * 60 + "/" + "e" * 60 + "/" + "f" * 60 + "/x [1].tif"): T16["a/IMG_20260809 (1).tif"]},
                 [S("enc", [run, f"{run}/{'g' * 60}/{'h' * 50}", "--mode", "2", "--no-preflight", sj])]),
        # ---- decoder ----------------------------------------------------------
        Scenario("dec_m0", MASTERS, [S("dec", [f"{run}/m", f"{run}/tif", "--mode", "0", sj])], quick=True),
        Scenario("dec_rerun", MASTERS, [
            S("dec", [f"{run}/m", f"{run}/tif", "--mode", "0", sj]),
            S("dec", [f"{run}/m", f"{run}/tif", "--mode", "0", sj])]),
        Scenario("dec_dry_run", MASTERS, [S("dec", [f"{run}/m", f"{run}/tif", "--mode", "0", "--dry-run", sj])],
                 writes=False),
        # ---- transcoder -------------------------------------------------------
        Scenario("tra_lossless", JPEGS, [S("tra", [f"{run}/j", f"{run}/jxl", sj])], quick=True),
        Scenario("tra_lossless_plain", JPEGS, [S("tra", [f"{run}/j", f"{run}/jxl", sj])], pct=False),
        Scenario("tra_lossless_rerun_back", JPEGS, [
            S("tra", [f"{run}/j", f"{run}/jxl", sj]),
            S("tra", [f"{run}/j", f"{run}/jxl", sj]),
            S("tra", [f"{run}/jxl", f"{run}/back", "--decode", sj])]),
        Scenario("tra_lossless_delete", JPEGS, [
            S("tra", [f"{run}/j", f"{run}/jxl", "--delete-source", "--delete-confirm-off", sj])]),
        Scenario("tra_png", PNGS, [S("tra", [f"{run}/p", f"{run}/jxl", "--force-convert", sj])]),
        Scenario("tra_jxl_to_jpeg_2048", MASTERS, [
            S("tra", [f"{run}/m", f"{run}/jpg", "--force-convert", "--decode", "--format", "jpeg",
                      "--quality", "70", "--resize-long", "2048", sj])]),
        # ---- recompressor -----------------------------------------------------
        Scenario("rec_plain_m2", MASTERS, [S("rec", [f"{run}/m", f"{run}/small", "--mode", "2", "--distance", "1", sj])], quick=True),
        Scenario("rec_derive_2048", MASTERS, [
            S("rec", [f"{run}/m", f"{run}/small", "--mode", "2", "--distance", "1", "--resize-long", "2048", sj]),
            S("rec", [f"{run}/m", f"{run}/small", "--mode", "2", "--distance", "1", "--resize-long", "2048", "--sync", sj])]),
        Scenario("rec_output_icc", MASTERS, [
            S("rec", [f"{run}/m", f"{run}/srgb", "--mode", "2", "--distance", "0.5", "--output-icc", "srgb", sj])]),
        Scenario("rec_sharpen", MASTERS, [
            S("rec", [f"{run}/m", f"{run}/sharp", "--mode", "2", "--distance", "1", "--resize-long", "1600",
                      "--sharpen", "screen", sj])]),
        Scenario("rec_inplace_m8", MASTERS, [
            S("rec", [run, "--mode", "8", "--distance", "1", "--delete-confirm-off", sj], stdin="y\n")]),
    ]


# --------------------------------------------------------------------------
# Observing a folder
# --------------------------------------------------------------------------

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _jxl_boxes(p: Path):
    data = p.read_bytes()
    if data[:2] == b"\xff\x0a":
        return [["bare", len(data)]], _sha(data)
    boxes, cs, i = [], hashlib.sha256(), 0
    while i + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[i:i + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = len(data) - i
        if size < hdr:
            boxes.append(["bad", size])
            break
        if typ == b"jxlc":
            cs.update(data[i + hdr:i + size])
        elif typ == b"jxlp":
            cs.update(data[i + hdr + 4:i + size])
        boxes.append([typ.decode("latin-1"), size])
        i += size
    return boxes, cs.hexdigest()[:16]


def _pixels(p: Path) -> str:
    try:
        if p.suffix.lower() in (".tif", ".tiff"):
            import tifffile
            with tifffile.TiffFile(str(p)) as tf:
                parts = [f"{pg.shape}{pg.dtype}:{_sha(pg.asarray().tobytes())}" for pg in tf.pages]
            return " ".join(parts)
        from PIL import Image
        with Image.open(p) as im:
            im.load()
            return f"{im.mode}{im.size}:{_sha(im.tobytes())}"
    except Exception as e:  # an unreadable file is an observation too
        return f"unreadable ({type(e).__name__})"


def _exiftool(paths: List[Path]) -> Dict[str, dict]:
    if not paths:
        return {}
    args = ["-charset", "filename=utf8", "-j", "-a", "-G1", "-struct",
            "-api", "largefilesupport=1"] + [str(p) for p in paths]
    r = subprocess.run(["exiftool", "-@", "-"], input=("\n".join(args) + "\n").encode("utf-8"),
                       capture_output=True, timeout=600)
    try:
        items = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    except json.JSONDecodeError:
        return {}
    return {os.path.normcase(os.path.normpath(d.get("SourceFile", ""))): d for d in items}


def _mask_icc_token(value):
    """A `jxlphoto-iccprofile:` token whose profile the toolkit generates at run
    time (the sRGB of --output-icc) carries that moment in the ICC header's
    creation date (bytes 24-35): compared without it, the rest byte for byte."""
    if not (isinstance(value, str) and value.startswith("jxlphoto-iccprofile:")):
        return value
    try:
        raw = bytearray(base64.b64decode(value.split(":", 1)[1], validate=True))
    except (ValueError, binascii.Error):
        return value
    if len(raw) >= 36:
        raw[24:36] = bytes(12)
    return f"jxlphoto-iccprofile:<icc without its date {_sha(bytes(raw))}>"


def _mask_meta(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if k == "SourceFile" or k.startswith(("System:", "ExifTool:")):
            continue
        out[k] = v
    hist = out.get("XMP-xmpMM:History")
    if isinstance(hist, list):
        out["XMP-xmpMM:History"] = [{**h, "When": "<when>"} if isinstance(h, dict) and "When" in h else h
                                    for h in hist]
    rel = out.get("XMP-dc:Relation")
    if isinstance(rel, list):
        out["XMP-dc:Relation"] = [_mask_icc_token(v) for v in rel]
    elif isinstance(rel, str):
        out["XMP-dc:Relation"] = _mask_icc_token(rel)
    return out


def _md5_file(p: Path) -> dict:
    names, bad = [], []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^([0-9a-fA-F]{32}) [ *](.+)$", line.strip())
        if not m:
            continue
        name = m.group(2)
        names.append(name)
        target = p.parent / name
        if not target.is_file() or hashlib.md5(target.read_bytes()).hexdigest() != m.group(1).lower():
            bad.append(name)
    return {"md5_names": sorted(names), "md5_mismatch": sorted(bad)}


def observe(run_dir: Path) -> Dict[str, dict]:
    files = sorted(p for p in run_dir.rglob("*") if p.is_file())
    meta = _exiftool([p for p in files if p.suffix.lower() in IMAGE_EXT | {".jxl"}])
    tree = {}
    for p in files:
        rel = p.relative_to(run_dir).as_posix()
        rec: dict = {"size": p.stat().st_size}
        ext = p.suffix.lower()
        if ext == ".jxl":
            rec["boxes"], rec["codestream"] = _jxl_boxes(p)
        elif ext in IMAGE_EXT:
            rec["pixels"] = _pixels(p)
        elif p.name.lower() == "checksums.md5":
            rec.update(_md5_file(p))
            rec.pop("size")
        else:
            rec["sha256"] = _sha(p.read_bytes())
        m = meta.get(os.path.normcase(os.path.normpath(str(p))))
        if m is not None:
            rec["meta"] = _mask_meta(m)
        if ext == ".jxl" or ext in IMAGE_EXT:
            # the bytes of a written file carry the History time: compared through
            # codestream/boxes/pixels/metadata instead
            rec.pop("size", None)
        tree[rel] = rec
    return tree


# --------------------------------------------------------------------------
# Running one side
# --------------------------------------------------------------------------

_MASKS = [
    (re.compile(r"^\d\d:\d\d:\d\d \| "), ""),                        # log line time
    (re.compile(r"\b\d{8}_\d{6}(_\d+)*(_\d+)?\.log\b"), "<log>"),     # log file name
    (re.compile(r"\b\d+(\.\d+)?\s?(ms|s|sec|secs|seconds|min|minutes|h)\b"), "<t>"),
    (re.compile(r"\b\d+m\s?\d+s\b|\b\d+:\d\d(:\d\d)?\b"), "<t>"),
    (re.compile(r"\b\d+(\.\d+)?\s?(MB|GB|files|MP|img)/s\b"), "<rate>"),
    (re.compile(r"ETA[^|]*"), "ETA <t>"),
    (re.compile(r"tmp[A-Za-z0-9_\-]{4,}"), "<tmp>"),
    (re.compile(r"\b[0-9a-f]{32}(?=_)"), "<uuid>"),                   # decoder temp names
    (re.compile(r"\.jxlphoto[\w\-]*\.tmp\b|\.tmp\d*\b"), "<.tmp>"),
]
_DROP = re.compile(r"\| (INFO|DEBUG) \| +Memory:|^Memory:|free RAM|available memory", re.I)
_COUNTER = re.compile(r"\[\s*\d+\s*/\s*\d+\s*\]\s*")


def _normalize(text: str, logs_dir: Path, code_dir: Path, lane: str) -> List[str]:
    out = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line or _DROP.search(line):
            continue
        for path, token in ((str(logs_dir), "<logs>"), (str(code_dir), "<code>")):
            for form in (path.replace("\\", "\\\\"), path, path.replace("\\", "/")):
                line = line.replace(form, token)
        for rx, token in _MASKS:
            line = rx.sub(token, line)
        if lane == "default":
            line = _COUNTER.sub("", line)
            if line.startswith(("WARNING | Workers", "INFO | Workers")):
                line = re.sub(r"\d+", "<n>", line)
        out.append(line)
    return sorted(out) if lane == "default" else out


def _summary(stdout: str) -> Optional[dict]:
    for line in stdout.splitlines():
        if line.startswith("##JXLSUM##"):
            try:
                d = json.loads(line[len("##JXLSUM##"):].strip())
            except json.JSONDecodeError:
                return {"unparsable": line}
            if not isinstance(d, dict):
                return d
            out = {}
            for k, v in d.items():
                if re.search(r"time|elapsed|second|duration", k, re.I):
                    v = "<t>"
                elif isinstance(v, str):
                    for rx, token in _MASKS:
                        v = rx.sub(token, v)
                out[k] = v
            return out
    return None


def _rmtree(path: Path) -> None:
    """rmtree that also removes read-only files (a fixture may be one)."""
    def retry(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if path.exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry)
        else:
            shutil.rmtree(path, onerror=retry)


def run_side(sc: Scenario, code_dir: Path, run_dir: Path, pristine: Path,
             logs_root: Path, lane: str) -> List[dict]:
    _rmtree(run_dir)
    shutil.copytree(pristine, run_dir)
    initial = observe(run_dir)
    steps = []
    for k, st in enumerate(sc.steps, 1):
        if st.script is None:
            kind, rel = st.action.split(":", 1)
            target = run_dir / rel
            if kind == "touch":
                t = target.stat().st_mtime + 10
                os.utime(target, (t, t))
            steps.append({"action": st.action, "tree": observe(run_dir)})
            continue
        logs = logs_root / f"step{k}"
        _rmtree(logs)
        logs.mkdir(parents=True)
        args = [a.replace("{run}", str(run_dir)) for a in st.args]
        if lane == "w1" and "--workers" not in args:
            args += ["--workers", "1"]
        env = {**os.environ, "JXLPHOTO_LOG_DIR": str(logs), "JXLPHOTO_SETTINGS": "",
               "JXLPHOTO_JXL_THUMB": "0", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        t0 = time.time()
        r = subprocess.run([sys.executable, "-B", str(code_dir / SCRIPT[st.script]), *args],
                           input=(st.stdin or "").encode("utf-8") if st.stdin else None,
                           stdin=None if st.stdin else subprocess.DEVNULL,
                           capture_output=True, env=env, timeout=STEP_TIMEOUT, cwd=str(run_dir.parent))
        out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
        log_text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                             for p in sorted(logs.rglob("*.log")))
        steps.append({
            "rc": r.returncode,
            "seconds": round(time.time() - t0, 1),
            "traceback": "Traceback (most recent call last)" in out,
            "summary": _summary(out),
            "stdout": _normalize(out, logs, code_dir, lane),
            "log": _normalize(log_text, logs, code_dir, lane),
            "tree": observe(run_dir),
        })
    if sc.writes and steps and steps[-1]["tree"] == initial:
        # A scenario meant to write that left the folder as it found it
        # compares SAME for nothing: a fixture or layout problem.
        steps[-1]["vacuous"] = True
    return steps


# --------------------------------------------------------------------------
# Comparing
# --------------------------------------------------------------------------

def _line_diff(tag: str, a: List[str], b: List[str]) -> List[str]:
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            out += [f"{tag} -: {x}" for x in a[i1:i2]]
        if op in ("replace", "insert"):
            out += [f"{tag} +: {x}" for x in b[j1:j2]]
    return out


def _tree_diff(a: Dict[str, dict], b: Dict[str, dict]) -> List[str]:
    out = []
    for rel in sorted(set(a) | set(b)):
        if rel not in b:
            out.append(f"file only in ref: {rel}")
            continue
        if rel not in a:
            out.append(f"file only in new: {rel}")
            continue
        ra, rb = a[rel], b[rel]
        for key in sorted(set(ra) | set(rb)):
            if key == "meta":
                ma, mb = ra.get("meta", {}), rb.get("meta", {})
                for t in sorted(set(ma) | set(mb)):
                    if ma.get(t) != mb.get(t):
                        out.append(f"{rel}: meta {t}: {json.dumps(ma.get(t), ensure_ascii=False)[:160]}"
                                   f" -> {json.dumps(mb.get(t), ensure_ascii=False)[:160]}")
            elif ra.get(key) != rb.get(key):
                out.append(f"{rel}: {key}: {json.dumps(ra.get(key), ensure_ascii=False)[:200]}"
                           f" -> {json.dumps(rb.get(key), ensure_ascii=False)[:200]}")
    return out


def compare(name: str, ref: List[dict], new: List[dict]) -> List[str]:
    diffs = []
    for k, (sa, sb) in enumerate(zip(ref, new), 1):
        pre = f"{name} | step {k} | "
        if "action" in sa:
            diffs += [pre + d for d in _tree_diff(sa["tree"], sb["tree"])]
            continue
        if sa["rc"] != sb["rc"]:
            diffs.append(pre + f"rc: {sa['rc']} -> {sb['rc']}")
        if sa.get("vacuous"):
            diffs.append(pre + "vacuous: the reference left the folder as it was "
                               "(fixture or layout problem: nothing was compared)")
        for side, s in (("ref", sa), ("new", sb)):
            if s["traceback"]:
                diffs.append(pre + f"traceback in {side}")
        if sa["summary"] != sb["summary"]:
            sma, smb = sa["summary"] or {}, sb["summary"] or {}
            if isinstance(sma, dict) and isinstance(smb, dict):
                for key in sorted(set(sma) | set(smb)):
                    if sma.get(key) != smb.get(key):
                        diffs.append(pre + f"summary {key}: {sma.get(key)} -> {smb.get(key)}")
            else:
                diffs.append(pre + f"summary: {sma} -> {smb}")
        diffs += [pre + d for d in _line_diff("stdout", sa["stdout"], sb["stdout"])]
        diffs += [pre + d for d in _line_diff("log", sa["log"], sb["log"])]
        diffs += [pre + d for d in _tree_diff(sa["tree"], sb["tree"])]
    if len(ref) != len(new):
        diffs.append(f"{name} | steps: {len(ref)} -> {len(new)}")
    return diffs


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def _materialize(spec: str, dest: Path) -> str:
    """The six files of `spec` (a revision, WORKTREE or a folder) into dest."""
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
            raise SystemExit(f"cannot read {f} at {spec}: {r.stderr.decode(errors='replace').strip()}")
        (dest / f).write_bytes(r.stdout)
    rev = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", spec],
                         capture_output=True, text=True).stdout.strip()
    return f"{spec} ({rev})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="\n\n".join(__doc__.split("\n\n")[1:]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, help="git revision of the reference side")
    ap.add_argument("--new", default="WORKTREE", help="WORKTREE (default), a revision or a folder")
    ap.add_argument("--fixtures", default=os.environ.get("JXLPHOTO_FIXTURES", r"E:\TESTE"))
    ap.add_argument("--quick", action="store_true", help="only the quick scenarios (~5 min)")
    ap.add_argument("--only", nargs="+", default=[], metavar="NAME", help="scenarios whose name contains NAME")
    ap.add_argument("--lane", choices=("w1", "default", "both"), default="w1")
    ap.add_argument("--expect", type=Path, help="regexes of the differences a phase means to make")
    ap.add_argument("--work", type=Path, help="scratch folder (default: <fixtures drive>\\jxlphoto_sbs)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch folder")
    ap.add_argument("--report", type=Path, help="write the report here too")
    ap.add_argument("--list", action="store_true", help="list the scenarios and exit")
    args = ap.parse_args(argv)

    scenarios = _scenarios()
    if args.list:
        for sc in scenarios:
            print(f"{'quick' if sc.quick else '     '}  {sc.name}  ({len(sc.steps)} step(s))")
        return 0
    if args.quick:
        scenarios = [s for s in scenarios if s.quick]
    if args.only:
        scenarios = [s for s in scenarios if any(o in s.name for o in args.only)]
    fixtures = Path(args.fixtures)
    expect = []
    if args.expect:
        expect = [re.compile(line.strip()) for line in args.expect.read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    work = args.work or Path(fixtures.anchor) / "jxlphoto_sbs"
    work.mkdir(parents=True, exist_ok=True)
    lanes = ["w1", "default"] if args.lane == "both" else [args.lane]

    ref_label = _materialize(args.ref, work / "code_ref")
    new_label = _materialize(args.new, work / "code_new")
    lines = [f"# Side by side — {time.strftime('%Y-%m-%d %H:%M')}", "",
             f"- ref: {ref_label}", f"- new: {new_label}", f"- lanes: {', '.join(lanes)}",
             f"- fixtures: {fixtures}", f"- expect: {args.expect or 'nothing'}", ""]
    unexpected_total, expected_total, ran = 0, 0, 0
    t_start = time.time()
    for sc in scenarios:
        missing = [v for v in sc.files.values() if not (fixtures / v).is_file()]
        if missing:
            lines.append(f"SKIPPED  {sc.name}: fixture missing: {missing[0]}")
            print(lines[-1], flush=True)
            continue
        root = work / (HOSTILE_PCT if sc.pct else HOSTILE_PLAIN)
        pristine = work / "pristine" / sc.name
        _rmtree(pristine)
        for dest, src in sc.files.items():
            target = pristine / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fixtures / src, target)
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)   # like the owner's files
        run_dir = root / sc.name
        for lane in lanes:
            t0 = time.time()
            # the same log folder on both sides (cleared per step): its path is
            # in the console output and in the summary
            ref = run_side(sc, work / "code_ref", run_dir, pristine, work / "logs", lane)
            new = run_side(sc, work / "code_new", run_dir, pristine, work / "logs", lane)
            diffs = compare(f"{sc.name} [{lane}]", ref, new)
            exp = [d for d in diffs if any(rx.search(d) for rx in expect)]
            unexp = [d for d in diffs if d not in exp]
            ran += 1
            expected_total += len(exp)
            unexpected_total += len(unexp)
            state = "DIFFERENT" if unexp else ("EXPECTED" if exp else "SAME")
            rcs = ",".join(str(s.get("rc", "-")) for s in ref)
            lines.append(f"{state:<9} {sc.name} [{lane}]  rc={rcs}  {time.time() - t0:.0f} s"
                         + (f"  ({len(unexp)} unexpected, {len(exp)} expected)" if diffs else ""))
            print(lines[-1], flush=True)
            for d in unexp[:40]:
                lines.append(f"    ! {d}")
                print(lines[-1], flush=True)
            if len(unexp) > 40:
                lines.append(f"    ! ... {len(unexp) - 40} more")
            for d in exp[:10]:
                lines.append(f"    ~ {d}")
            (work / "obs").mkdir(exist_ok=True)
            (work / "obs" / f"{sc.name}.{lane}.json").write_text(
                json.dumps({"ref": ref, "new": new}, ensure_ascii=False, indent=1), encoding="utf-8")
        _rmtree(run_dir)
    lines += ["", f"{ran} run(s), {unexpected_total} unexpected difference(s), "
                  f"{expected_total} expected, {time.time() - t_start:.0f} s"]
    print(lines[-1])
    report = args.report or REPO / "AI_tools" / f"{time.strftime('%y%m%d')}_side_by_side_report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {report}")
    if not args.keep:
        for sub in ("pristine", "logs", HOSTILE_PCT, HOSTILE_PLAIN):
            _rmtree(work / sub)
    if ran == 0:
        return 2
    return 1 if unexpected_total else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
