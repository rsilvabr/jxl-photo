#!/usr/bin/env python3
"""Real-photo battery: the four scripts end to end on real files.

The pytest suite is synthetic and mostly mocked; codec-path bugs (ICC, bit
depth, multi-page, metadata, exiftool output formats, result tuples that only a
real run consumes) only show up against real files. This battery runs every
script as a SUBPROCESS, with stdin closed (exactly like an unattended run),
on COPIES of real photos, and checks the files on disk afterwards.

Run it before every release, and after any change to what the codecs or
exiftool read/write, to a delete gate, or to the in-place paths:

    py tools/real_photo_battery.py --fixtures <folder>
    (or set JXLPHOTO_FIXTURES=<folder> and omit the flag)

Fixtures (never modified — everything is copied to a scratch folder first):
    <fixtures>/TIFF16/*.tif     at least 3 single-image 16-bit RGB TIFFs with an
                                ICC profile (camera/Capture One exports)
    <fixtures>/raw_scan/*.tif   optional: a multi-page film scan
                                [image, thumbnail, IR] (the M/R checks)
    <fixtures>/TIFF16_varied/*.tif  optional: many different exports (cameras,
                                phones, scanners, ICC versions, ISO, sizes,
                                smooth to noisy), each encoded and decoded
    <fixtures>/TIFF_scans_varied/*.tif  optional: film scans and odd TIFFs
                                (8/16-bit gray, no ICC, scanner profiles,
                                RGB+IR / alpha 4th sample, LZW, 84 MP),
                                lossless bit-exact and lossy colour checks
    <fixtures>/JPEG_varied/*.jpg     optional: JPEG structures (progressive,
                                4:4:0, gray, MPF, odd ICCs, broken MakerNotes,
                                an empty and a non-JPEG file) through the
                                transcoder, bit-exact, and its deleting mode
    <fixtures>/silverfast_xmp/*.tif  optional: ORIGINAL SilverFast scans (image,
                                thumbnail, IR, SilverFast's LSI/ XMP and private
                                tags); the smallest one round-trips with all of it
    <fixtures>/TIFF_tagless/<camera>/*.tif  optional: camera TIFFs that lost every
                                tag (image + 160x120 thumbnail page) and one as the
                                camera wrote it: none may count as a film scan

The scratch folder (default <fixtures drive>\\jxlphoto_battery_<date>) is
deleted at the end unless --keep. Every check runs inside a HOSTILE folder
below it (Japanese, [ ], parentheses, a space and %, on another drive than
%TEMP%), the kind of path the owner's library is full of; --ascii-paths runs
them in a plain folder instead, to tell a path bug from any other. The report is written to
AI_tools/<YYMMDD>_battery_report.md (gitignored) and printed. Exit code 0 when
every check passed, 1 otherwise. Needs cjxl, djxl, exiftool and magick on PATH.
"""
import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import tifffile

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
ENC, DEC, REC, TR = (str(REPO / n) for n in (
    "jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_recompressor.py",
    "jxl_jpeg_transcoder.py"))

results = []        # (section, name, ok, detail)
# The folder every check runs in (see the docstring). The owner's library has
# all of these in one path: G:\2025\250118_吾妻山公園_菜の花 [FINAL]\01_Z7\...
HOSTILE_DIR = "250118_吾妻山公園 [FINAL] (2) 100%"
B = LOGS = None
_section = ""


def section(name):
    global _section
    _section = name
    print(f"\n== {name}", flush=True)


def check(name, ok, detail=""):
    results.append((_section, name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""),
          flush=True)


def run(tag, args, timeout=7200):
    """One script run as an unattended subprocess (stdin closed)."""
    t = time.time()
    r = subprocess.run([PY] + [str(a) for a in args], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, cwd=str(B),
                       env={**os.environ, "JXLPHOTO_LOG_DIR": str(B / "_script_logs")})
    out = (r.stdout or "") + (r.stderr or "")
    (LOGS / f"{tag}.log").write_text(
        f"$ {' '.join(map(str, args))}\nrc={r.returncode} ({time.time() - t:.0f}s)\n\n{out}",
        encoding="utf-8")
    print(f"   [{tag}] rc={r.returncode} {time.time() - t:.0f}s", flush=True)
    if "Traceback (most recent call last)" in out:
        check(f"{tag}: no Python traceback", False, f"see {LOGS / (tag + '.log')}")
    return r.returncode, out


def exif(args):
    """exiftool through a UTF-8 argfile, as the scripts call it: on the command
    line Windows hands exiftool the path in the ANSI code page (Japanese arrives
    mangled), so a hostile scratch folder would fail the battery's own reads."""
    fd, argfile = tempfile.mkstemp(suffix=".args")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("-charset\nfilename=utf8\n" + "\n".join(str(a) for a in args) + "\n")
        r = subprocess.run(["exiftool", "-@", argfile], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
    finally:
        os.unlink(argfile)
    return r.stdout.strip()


def relation(path):
    """XMP-dc:Relation values as a list. exiftool -j keeps list items separate,
    while -s3 would join them with ', ' on one line."""
    try:
        rel = json.loads(exif(["-j", "-XMP-dc:Relation", path]))[0].get("Relation")
    except Exception:
        return []
    return [] if rel is None else [str(v) for v in (rel if isinstance(rel, list) else [rel])]


def history(path):
    """xmpMM:History entries as read by `exiftool -struct -j` (a list)."""
    try:
        h = json.loads(exif(["-struct", "-j", "-XMP-xmpMM:History", path]))[0].get("History")
    except Exception:
        return []
    return [] if h is None else (h if isinstance(h, list) else [h])


def _last_history_entry(path, action, parameters, label):
    """check() that the LAST xmpMM:History entry of `path` is this toolkit's."""
    h = history(path)
    ok = (bool(h) and h[-1].get("Action") == action
          and str(h[-1].get("SoftwareAgent", "")).startswith("jxl-photo ")
          and h[-1].get("Parameters") == parameters)
    check(label, ok, f"{len(h)} entries" if h else "no History")


def _count_history(path):
    return len(history(path))


def origins_of(path):
    """The jxlphoto-origin tokens of a file (a list, so a duplicate shows)."""
    return [tok for tok in relation(path) if tok.startswith("jxlphoto-origin:")]


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def touch(p, offset):
    t = time.time() + offset
    os.utime(p, (t, t))


def rmtree(path):
    """shutil.rmtree that also removes read-only files (fixtures rescued from
    CDs keep the attribute, and copy2 carries it over)."""
    def _clear(func, p, _exc):
        os.chmod(p, 0o666)
        func(p)
    shutil.rmtree(path, onexc=_clear)


def cp(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def raw_rgb16_bytes(png):
    """A PNG's pixels as raw little-endian 16-bit RGB, read from stdin: on the
    command line ImageMagick would glob '[ ]' and expand '%' in the path."""
    return subprocess.run(["magick", "png:-", "-depth", "16", "-endian", "LSB", "rgb:-"],
                          input=Path(png).read_bytes(), capture_output=True).stdout


def raw_rgb16(png, shape):
    """djxl's own PNG as raw little-endian 16-bit RGB (no colour management)."""
    a = np.frombuffer(raw_rgb16_bytes(png), dtype="<u2")
    return a.reshape(shape) if a.size == int(np.prod(shape)) else None


def _via_ascii(cmd_in_out, src, dst):
    """Run a codec tool on an ASCII-named copy in %TEMP% and move the output to
    `dst`: djxl opens paths in the ANSI code page, and magick globs/expands its
    file names. Returns the CompletedProcess."""
    tmp = Path(tempfile.mkdtemp(prefix="battery_"))
    try:
        s = tmp / ("in" + Path(src).suffix) if src is not None else None
        if s is not None:
            shutil.copyfile(src, s)
        o = tmp / ("out" + Path(dst).suffix)
        r = subprocess.run(cmd_in_out(s, o), capture_output=True)
        if o.exists():
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(o), str(dst))
        return r
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def djxl(src, dst, *extra):
    return _via_ascii(lambda s, o: ["djxl", str(s), str(o), *extra], src, dst)


def pick_fixtures(fx: Path):
    tiffs = sorted((fx / "TIFF16").glob("*.tif"), key=lambda p: p.stat().st_size)
    if len(tiffs) < 3:
        sys.exit(f"need at least 3 TIFFs in {fx / 'TIFF16'}")
    scan = None
    for p in sorted((fx / "raw_scan").glob("*.tif")) if (fx / "raw_scan").is_dir() else []:
        with tifffile.TiffFile(p) as t:
            if len(t.pages) >= 3:
                scan = p
                break
    return tiffs[:3], scan


# ─────────────────────────────────────────────────────────────────────────────

def encoder_checks(A1, A2, A3, pool_jxl):
    section("Encoder (TIFF -> JXL)")
    FAST = ["--distance", "1", "--effort", "3", "--workers", "8", "--no-preflight"]

    origins = origins_of(pool_jxl / A1.with_suffix(".jxl").name)
    check("a clean 16-bit TIFF master records jxlphoto-origin:tiff16",
          origins == ["jxlphoto-origin:tiff16"], str(origins))

    out_jxl = pool_jxl / A1.with_suffix(".jxl").name
    _last_history_entry(out_jxl, "converted",
                        "jxl_tiff_encoder: from image/tiff to image/jxl",
                        "encoder: the output carries this run's xmpMM:History entry")
    check("encoder: the output has the source's History entries + 1",
          _count_history(out_jxl) == _count_history(A1) + 1,
          f"source {_count_history(A1)} -> output {_count_history(out_jxl)}")

    # The fixtures may carry no History at all; seed one on a COPY so the
    # "earlier entries are kept" claim is really exercised.
    e = B / "e_history"
    cp(A1, e / "hist.tif")
    exif(["-q", "-overwrite_original",
          "-XMP-xmpMM:History={Action=saved,When=2026:10:01 10:00:00,"
          "SoftwareAgent=Capture One 16}", e / "hist.tif"])
    rc, _ = run("enc_history", [ENC, e, "--mode", "1"] + FAST)
    h = history(e / "converted_jxl" / "hist.jxl")
    check("encoder: an earlier History entry (Capture One) is kept, ours appended",
          rc == 0 and len(h) == 2 and h[0].get("SoftwareAgent") == "Capture One 16"
          and h[0].get("Action") == "saved"
          and h[1].get("Parameters") == "jxl_tiff_encoder: from image/tiff to image/jxl",
          f"rc={rc} {len(h)} entries")

    e = B / "e_caption"
    cp(A1, e / "cap.tif")
    exif(["-q", "-overwrite_original",
          "-XMP-dc:Description=[minor] Captured: autumn 1958", e / "cap.tif"])
    rc, _ = run("enc_caption", [ENC, e, "--mode", "1"] + FAST)
    desc = exif(["-s3", "-XMP-dc:Description", e / "converted_jxl" / "cap.jxl"])
    check("caption carried verbatim, lineage appended",
          rc == 0 and desc.startswith("[minor] Captured: autumn 1958 | gen=1"), desc)

    e = B / "e_own"
    cp(A1, e / "own.tif")
    run("enc_own_encode", [ENC, e, "--mode", "8"] + FAST)
    rc, _ = run("enc_own_delete", [ENC, e, "--mode", "8", "--sync", "--delete-source",
                                   "--delete-skipped", "--delete-confirm-off"] + FAST)
    check("--delete-skipped: source of its OWN archive is deleted",
          rc == 0 and not (e / "own.tif").exists() and (e / "own.jxl").exists(), f"rc={rc}")

    e = B / "e_foreign"
    cp(A2, e / "photo.tif")
    cp(pool_jxl / A3.with_suffix(".jxl").name, e / "photo.jxl")
    touch(e / "photo.tif", -3600)
    touch(e / "photo.jxl", 0)
    rc, out = run("enc_foreign", [ENC, e, "--mode", "8", "--sync", "--delete-source",
                                  "--delete-skipped", "--delete-confirm-off"] + FAST)
    check("--delete-skipped: newer same-named JXL of ANOTHER photo keeps the master",
          (e / "photo.tif").exists() and "provenance check failed" in out, f"rc={rc}")

    e = B / "e_legacy"
    cp(A1, e / "legacy.tif")
    run("enc_legacy_encode", [ENC, e, "--mode", "8"] + FAST)
    exif(["-q", "-overwrite_original", "-XMP-dc:Relation=", e / "legacy.jxl"])
    touch(e / "legacy.tif", -3600)
    touch(e / "legacy.jxl", 0)
    rc, out = run("enc_legacy", [ENC, e, "--mode", "8", "--sync", "--delete-source",
                                 "--delete-skipped", "--delete-confirm-off"] + FAST)
    check("--delete-skipped: markerless (pre-v2.0.0) archive keeps the source, with hint",
          (e / "legacy.tif").exists() and "--provenance adopt" in out, f"rc={rc}")

    rc, _ = run("enc_allskip", [ENC, e, "--mode", "8", "--sync", "--delete-source"] + FAST)
    check("all-skip plan + --delete-source, no TTY: no prompt, exit 0, nothing deleted",
          rc == 0 and (e / "legacy.tif").exists(), f"rc={rc}")
    e = B / "e_confirm"
    cp(A3, e / "new.tif")
    rc, _ = run("enc_confirm", [ENC, e, "--mode", "8", "--sync", "--delete-source"] + FAST)
    check("a plan that WOULD delete still asks (exit 3 on a closed stdin)",
          rc == 3 and (e / "new.tif").exists() and not (e / "new.jxl").exists(), f"rc={rc}")

    e = B / "e_marker"
    cp(A1, e / "sess" / "_EXPORT" / "x.tif")
    rc, out = run("enc_empty_marker", [ENC, e, "--mode", "6", "--export-marker", "",
                                       "--dry-run"] + FAST)
    rc2, out2 = run("enc_default_marker", [ENC, e, "--mode", "6", "--dry-run"] + FAST)
    check('--export-marker "" finds nothing; the default marker finds the file',
          " DRY " not in out and " DRY" in out2 and "x.tif" in out2, f"rc={rc}/{rc2}")

    e = B / "e_exclude"
    cp(A1, e / "keep" / "k.tif")
    cp(A2, e / "skipme" / "s.tif")
    rc, _ = run("enc_exclude", [ENC, e, "--mode", "8", "--exclude-folders", "SKIPME"] + FAST)
    check("--exclude-folders (folder name, case-insensitive) skips that tree",
          rc == 0 and (e / "keep" / "k.jxl").exists()
          and not (e / "skipme" / "s.jxl").exists())


def decoder_checks(A1, pool_jxl):
    section("Decoder (JXL -> TIFF)")
    for dist in ("1", "0.1"):
        d = B / f"d_px_{dist}"
        cp(A1, d / "src" / "x.tif")
        run(f"dec_px_{dist}_encode", [ENC, d / "src", "--mode", "1", "--distance", dist,
                                      "--effort", "3", "--no-preflight"])
        jxl = d / "src" / "converted_jxl" / "x.jxl"
        cp(jxl, d / "dec" / "x.jxl")
        rc, _ = run(f"dec_px_{dist}", [DEC, d / "dec", "--mode", "1"])
        out_t = d / "dec" / "converted_tiff" / "x.tif"
        if rc != 0 or not out_t.exists():
            check(f"d={dist}: decode", False, f"rc={rc}")
            continue
        with tifffile.TiffFile(A1) as t0, tifffile.TiffFile(out_t) as t1:
            icc_same = (t0.pages[0].tags.get(34675) is not None
                        and t1.pages[0].tags.get(34675) is not None
                        and t0.pages[0].tags[34675].value == t1.pages[0].tags[34675].value)
            master = t0.pages[0].asarray().astype(np.float64)
            dec = t1.pages[0].asarray()
        ref_png = d / "ref.png"
        djxl(jxl, ref_png, "--bits_per_sample=16")
        ref = raw_rgb16(ref_png, dec.shape)
        mse = float(np.mean((master - dec.astype(np.float64)) ** 2))
        psnr = 10 * np.log10(65535.0 ** 2 / mse) if mse else 999.0
        check(f"d={dist}: original ICC restored byte-for-byte", icc_same)
        check(f"d={dist}: pixels == an independent djxl decode",
              ref is not None and np.array_equal(ref, dec), f"PSNR vs master {psnr:.1f} dB")
        _last_history_entry(out_t, "converted",
                            "jxl_tiff_decoder: from image/jxl to image/tiff",
                            f"d={dist}: the decode appends this run's History entry")
        check(f"d={dist}: the decoded TIFF has one History entry more than the JXL",
              _count_history(out_t) == _count_history(jxl) + 1,
              f"jxl {_count_history(jxl)} -> tiff {_count_history(out_t)}")

    a = B / "d_own"
    cp(pool_jxl / A1.with_suffix(".jxl").name, a / "p.jxl")
    run("dec_own_decode", [DEC, a, "--mode", "1"])
    f = B / "d_foreign"
    other = next(p for p in sorted(pool_jxl.glob("*.jxl"))
                 if p.name != A1.with_suffix(".jxl").name)
    cp(other, f / "p.jxl")                                               # another photo
    cp(a / "converted_tiff" / "p.tif", f / "converted_tiff" / "p.tif")    # a's decode
    touch(f / "p.jxl", -3600)
    touch(f / "converted_tiff" / "p.tif", 0)
    rc, out = run("dec_foreign", [DEC, f, "--mode", "1", "--sync", "--delete-source",
                                  "--delete-skipped", "--delete-confirm-off"])
    # Since the 2026-10-08 audit (X1/D1) the smart sync itself refuses to call
    # another JXL's decode "up to date" ("... marker of a DIFFERENT JXL"), so
    # the skip never reaches the --delete-skipped gate (whose KEEP says
    # "MATCHING"); either refusal keeps the JXL.
    check("--delete-skipped: a decoded TIFF copied next to a DIFFERENT p.jxl keeps it",
          (f / "p.jxl").exists() and ("DIFFERENT JXL" in out or "MATCHING" in out),
          f"rc={rc}")
    rc, _ = run("dec_allskip", [DEC, a, "--mode", "1", "--sync", "--delete-source"])
    check("all-skip plan + --delete-source, no TTY: exit 0, nothing deleted",
          rc == 0 and (a / "p.jxl").exists(), f"rc={rc}")
    rc, _ = run("dec_own_delete", [DEC, a, "--mode", "1", "--sync", "--delete-source",
                                   "--delete-skipped", "--delete-confirm-off"])
    check("--delete-skipped: its OWN decode certifies the source (deleted)",
          rc == 0 and not (a / "p.jxl").exists(), f"rc={rc}")

    # D5 (round 50): the decode's pixel record (jxlphoto-pixsum) on a real
    # 16-bit TIFF with its JPEG preview page — an edit saved the way Photoshop
    # saves one (new pixels, same XMP) must not be decoded over, and an
    # untouched decode must still be refreshed.
    e = B / "d_edited"
    cp(pool_jxl / A1.with_suffix(".jxl").name, e / "p.jxl")
    run("dec_edit_decode", [DEC, e, "--mode", "1"])
    t = e / "converted_tiff" / "p.tif"
    touch(e / "p.jxl", 3600)
    rc, out = run("dec_edit_untouched", [DEC, e, "--mode", "1"])
    check("an untouched decode is refreshed when its JXL is newer",
          rc == 0 and "reconverting" in out and "EDITED" not in out, f"rc={rc}")
    orig = cp(t, e / "orig.tif")
    with tifffile.TiffFile(t) as tf:
        arr = tf.pages[0].asarray().copy()
    arr[:64, :64] = 0
    tifffile.imwrite(t, arr, photometric="rgb", compression="zlib", metadata=None)
    exif(["-q", "-overwrite_original", "-tagsfromfile", orig, "-all:all", t])
    orig.unlink()
    touch(e / "p.jxl", 7200)
    before = md5(t)
    rc, out = run("dec_edit_sync", [DEC, e, "--mode", "1"])
    check("an EDITED decode is not decoded over (JXL newer, marker intact)",
          md5(t) == before and "EDITED" in out, f"rc={rc}")


def multipage_and_recompressor_checks(A1, pool_jxl, scan):
    section("Multi-page scan + recompressor in place")
    m = B / "m"
    cp(scan, m / "scan.tif")
    rc, _ = run("mp_encode", [ENC, m, "--mode", "1", "--distance", "0", "--effort", "2",
                              "--workers", "8", "--no-preflight"])
    pages = sorted((m / "converted_jxl").glob("*.jxl"))
    check("multi-page scan split into page JXLs (lossless)", rc == 0 and len(pages) >= 2,
          str([p.name for p in pages]))
    md = B / "m_dec"
    for p in pages:
        cp(p, md / p.name)
    rc, _ = run("mp_decode", [DEC, md, "--mode", "1", "--workers", "4"])
    rt = list((md / "converted_tiff").glob("*.tif"))
    ok, detail = False, f"rc={rc} tiffs={len(rt)}"
    if rc == 0 and len(rt) == 1:
        with tifffile.TiffFile(scan) as t0, tifffile.TiffFile(rt[0]) as t1:
            p0 = [pg for pg in t0.pages if pg.shape[0] > 2000]
            p1 = [pg for pg in t1.pages if pg.shape[0] > 2000]
            ok = len(p0) == len(p1) and all(
                np.array_equal(x.asarray(), y.asarray()) for x, y in zip(p0, p1))
            detail = f"full-size pages {len(p0)} -> {len(p1)}"
    check("reconstructed TIFF: every full-size page (RGB + IR) bit-identical", ok, detail)

    # v2.17.0: a film scan is never re-encoded lossy unless asked — the
    # default copies every page verbatim.
    g = B / "r_scan_guard"
    for p in pages:
        cp(p, g / p.name)
    before_g = {p.name: md5(p) for p in g.glob("*.jxl")}
    rc, out = run("rec_scan_guard", [REC, g, "--mode", "8", "--distance", "2", "--effort", "3",
                                     "--no-preflight", "--workers", "8", "--delete-confirm-off"])
    check("film scan: the recompressor copies every page verbatim (--on-scan copy)",
          rc == 0 and all(md5(g / n) == h for n, h in before_g.items())
          and "scan=copy" in out and not list(g.glob("*.tmp")), f"rc={rc}")

    # The rest re-encodes the scan on purpose (--on-scan convert): it is what
    # exercises in-place group replacement, the colours and the veto.
    ARGS = ["--mode", "8", "--distance", "2", "--effort", "3", "--on-unknown", "convert",
            "--on-regeneration", "convert", "--on-downgrade", "convert", "--no-preflight",
            "--workers", "8", "--delete-confirm-off", "--on-scan", "convert"]
    r = B / "r_inplace"
    for p in pages:
        cp(p, r / p.name)
    cp(pool_jxl / A1.with_suffix(".jxl").name, r / "single.jxl")
    before = {p.name: md5(p) for p in r.glob("*.jxl")}
    hist_before = {p.name: _count_history(p) for p in r.glob("*.jxl")}
    rc, _ = run("rec_inplace", [REC, r] + ARGS)
    changed = sorted(n for n in before if md5(r / n) != before[n])
    check("in place: a single file AND every page of a group replaced, no .tmp left",
          rc == 0 and len(changed) == len(before) and not list(r.glob("*.tmp")),
          f"rc={rc} changed={changed}")
    check("in place: jxlphoto-mpg markers kept on the replaced pages",
          all("jxlphoto-mpg" in exif(["-s3", "-XMP-dc:Relation", r / p.name]) for p in pages))
    lin = exif(["-s3", "-XMP-dc:Description", r / "single.jxl"])
    check("in place: lineage records the second generation", "gen=2" in lin, lin[:120])
    # The single file goes straight through cjxl (its XMP carried, #531), the
    # scan pages through the 'skip' route (decoded, no carried XMP): both must
    # gain exactly ONE entry, the full one even from the '<uuid>_<stem>.tmp'.
    grown = {n: _count_history(r / n) - hist_before[n] for n in hist_before}
    check("in place: every re-encode has exactly one History entry more",
          all(v == 1 for v in grown.values()), str(grown))
    _last_history_entry(r / "single.jxl", "converted",
                        "jxl_recompressor: from image/jxl to image/jxl",
                        "in place: the recompressor's entry names both types")

    # R1 (2026-10-08 audit): the COLOURS, not only markers and lineage. The
    # scan's profile (a film scanner's: A2B tables, no B2A) has no native JXL
    # form, and before the fix this very in-place d=2 re-encode came back at
    # ~27 dB through the decoder (18 dB one generation later) while every
    # check above passed.
    rd = B / "r_inplace_dec"
    for p in pages:
        cp(r / p.name, rd / p.name)
    rc, _ = run("rec_inplace_decode", [DEC, rd, "--mode", "1", "--workers", "4"])
    rt = list((rd / "converted_tiff").glob("*.tif"))
    ok, detail = False, f"rc={rc} tiffs={len(rt)}"
    if rc == 0 and len(rt) == 1:
        with tifffile.TiffFile(scan) as t0, tifffile.TiffFile(rt[0]) as t1:
            a = [pg for pg in t0.pages if pg.shape[0] > 2000]
            b = [pg for pg in t1.pages if pg.shape[0] > 2000]
            if a and b and a[0].shape == b[0].shape:
                x, y = a[0].asarray(), b[0].asarray()
                sse = 0.0
                for i in range(0, x.shape[0], 256):      # row blocks: low memory
                    d = x[i:i + 256].astype(np.float64) - y[i:i + 256].astype(np.float64)
                    sse += float((d * d).sum())
                mse = sse / x.size
                psnr = float("inf") if mse == 0 else 10 * np.log10(65535.0 ** 2 / mse)
                ok = psnr >= 40.0
                detail = f"RGB page vs the original scan: {psnr:.1f} dB (d=2)"
            else:
                detail = "page shapes differ"
    check("in place: the re-encoded scan keeps its colours (decoder vs original)",
          ok, detail)

    r = B / "r_veto"
    for p in pages:
        cp(p, r / p.name)
    victim = r / pages[-1].name
    data = bytearray(victim.read_bytes())
    data[len(data) // 2:len(data) // 2 + 65536] = os.urandom(65536)   # codestream only
    victim.write_bytes(bytes(data))
    sib = {p.name: md5(p) for p in r.glob("*.jxl") if p != victim}
    rc, out = run("rec_veto", [REC, r] + ARGS)
    check("group veto: a page that fails leaves its siblings untouched, no .tmp",
          all(md5(r / n) == h for n, h in sib.items()) and not list(r.glob("*.tmp"))
          and "GROUP HELD" in out, f"rc={rc}")


def recompressor_foreign_check(A1, A3, pool_jxl):
    section("Recompressor (mode 1)")
    r = B / "r_foreign"
    cp(pool_jxl / A1.with_suffix(".jxl").name, r / "p.jxl")
    common = ["--distance", "2", "--effort", "3", "--on-unknown", "convert", "--no-preflight"]
    run("rec_first", [REC, r, "--mode", "1"] + common)
    outs = [p for p in r.rglob("*.jxl") if p.parent != r]
    if not outs:
        check("overwriting an output of ANOTHER origin is refused", False, "no output")
        return
    cp(pool_jxl / A3.with_suffix(".jxl").name, outs[0])
    touch(outs[0], -7200)
    touch(r / "p.jxl", 0)
    _before = md5(outs[0])
    rc, out = run("rec_foreign", [REC, r, "--mode", "1", "--sync"] + common)
    # 2026-10-08 audit (X1/R2): a warning used to be all this got, and the
    # archive of another photo was overwritten anyway. It is a refusal now:
    # exit 1, the existing output untouched.
    check("overwriting an output of ANOTHER origin is refused (archive untouched)",
          rc == 1 and "different origin" in out and md5(outs[0]) == _before,
          f"rc={rc}")


def overwrite_guard_checks(A1, A2):
    section("Renamed folders and the overwrite guard")
    sys.path.insert(0, str(REPO))
    import jxl_tiff_encoder as _enc

    def _src(path):
        return [t for t in relation(path) if t.startswith("jxlphoto-src:")]

    def _srcsum(path):
        return [t for t in relation(path) if t.startswith("jxlphoto-srcsum:")]

    def _pixels_md5(png):
        return hashlib.md5(raw_rgb16_bytes(png)).hexdigest()

    FAST8 = ["--mode", "8", "--distance", "1", "--effort", "3", "--no-preflight"]

    # (a) renamed folder + re-export: refused by default, overwritten with the
    # guard off (the record then names the new path).
    g = B / "og1" / "2024-05 Viagem"
    cp(A1, g / "a.tif")
    rc, _ = run("og1_encode", [ENC, B / "og1"] + FAST8)
    (B / "og1" / "2024-05 Viagem").rename(B / "og1" / "2024-05 Kyoto")
    g = B / "og1" / "2024-05 Kyoto"
    cp(A2, g / "a.tif")                       # re-exported, other pixels
    touch(g / "a.tif", 60)
    jxl = g / "a.jxl"
    rc, out = run("og1_refused", [ENC, B / "og1"] + FAST8 + ["--sync"])
    check("renamed + re-exported: refused by default, message points to the docs",
          rc == 1 and _enc.GUARD_DOC_URL in out and "--no-overwrite-guard" in out,
          f"rc={rc}")
    before = _src(jxl)
    rc, out = run("og1_guard_off", [ENC, B / "og1"] + FAST8 + ["--sync",
                                                               "--no-overwrite-guard"])
    check("guard off: re-encoded, the record names the new path",
          rc == 0 and "overwrite guard off" in out
          and _src(jxl) == [f"jxlphoto-src:{_enc._source_path_id(g / 'a.tif')}"]
          and _src(jxl) != before, f"rc={rc} src={_src(jxl)}")

    # (b) renamed folder + --restamp-moved: the record is rewritten, the pixels,
    # the file date and the History are untouched; a later re-export syncs.
    g = B / "og2" / "2024-05 Viagem"
    cp(A1, g / "a.tif")
    rc, _ = run("og2_encode", [ENC, B / "og2"] + FAST8)
    (B / "og2" / "2024-05 Viagem").rename(B / "og2" / "2024-05 Kyoto")
    g = B / "og2" / "2024-05 Kyoto"
    jxl = g / "a.jxl"
    png_before = B / "og2_before.png"
    djxl(jxl, png_before)
    md5_before = _pixels_md5(png_before)
    mtime_before = jxl.stat().st_mtime_ns
    hist_before = _count_history(jxl)
    src_before, srcsum_before = _src(jxl), _srcsum(jxl)

    rc, out = run("og2_restamp", [ENC, B / "og2"] + FAST8 + ["--restamp-moved"])
    png_after = B / "og2_after.png"
    djxl(jxl, png_after)
    check("restamp: record rewritten, pixels/date/History unchanged",
          rc == 0 and "RESTAMP |" in out
          and _src(jxl) == [f"jxlphoto-src:{_enc._source_path_id(g / 'a.tif')}"]
          and _src(jxl) != src_before
          and _srcsum(jxl) == srcsum_before
          and _pixels_md5(png_after) == md5_before
          and jxl.stat().st_mtime_ns == mtime_before
          and _count_history(jxl) == hist_before
          and not list(g.glob("*.tmp")), f"rc={rc}")

    cp(A2, g / "a.tif")                       # re-exported after the restamp
    touch(g / "a.tif", 60)
    rc, out = run("og2_sync", [ENC, B / "og2"] + FAST8 + ["--sync"])
    check("after restamp, a re-export syncs without a refusal",
          rc == 0 and "REFUSING" not in out, f"rc={rc}")


def memory_cap_checks(A1, pool_jxl):
    section("Worker memory cap")
    with tifffile.TiffFile(A1) as _t:
        _mp_expected = (f"{int(_t.pages[0].imagewidth) * int(_t.pages[0].imagelength) / 1e6:.0f}")
    e = B / "mem_enc"
    cp(A1, e / "a.tif")
    rc, out = run("mem_enc", [ENC, e, "--mode", "1", "--distance", "3",
                              "--effort", "7", "--workers", "512",
                              "--no-preflight"])
    check("encoder: --workers 512 at d=3 e=7 is reduced, output written",
          rc == 0 and "--workers 512 reduced to" in out
          and len(list((e / "converted_jxl").glob("*.jxl"))) == 1, f"rc={rc}")
    _m = re.search(r"Memory: ~[\d.]+ GB per worker \([a-z-]+ encode, (\d+) MP", out)
    check(f"encoder: the cap read the real image size ({_mp_expected} MP)",
          bool(_m) and _m.group(1) == _mp_expected,
          f"log={_m.group(1) if _m else 'none'} expected={_mp_expected}")
    r = B / "mem_rec"
    cp(pool_jxl / A1.with_suffix(".jxl").name, r / "p.jxl")
    rc, out = run("mem_rec", [REC, r, "--mode", "1", "--distance", "3",
                              "--effort", "7", "--workers", "512",
                              "--on-unknown", "convert", "--on-regeneration",
                              "convert", "--on-downgrade", "convert",
                              "--no-preflight"])
    outs = [p for p in r.rglob("*.jxl") if p.parent != r]
    check("recompressor: --workers 512 at d=3 e=7 is reduced, output decodes",
          rc == 0 and "--workers 512 reduced to" in out and len(outs) == 1
          and djxl(outs[0], B / "mem_rec.png").returncode == 0, f"rc={rc}")
    _m = re.search(r"Memory: ~[\d.]+ GB per cjxl \([a-z-]+ encode, (\d+) MP", out)
    check(f"recompressor: the cap read the real image size ({_mp_expected} MP)",
          bool(_m) and _m.group(1) == _mp_expected,
          f"log={_m.group(1) if _m else 'none'} expected={_mp_expected}")
    _s = re.search(r"workers (\d+), cjxl at a time (\d+)", out)
    check("recompressor: cjxl slots logged, never more than the workers",
          bool(_s) and 1 <= int(_s.group(2)) <= int(_s.group(1)) <= 512,
          f"log={_s.group(0) if _s else 'none'}")


def derivative_rederive_checks(A1, pool_jxl):
    section("Derivative re-derive on encode change")
    r = B / "rederive"
    cp(pool_jxl / A1.with_suffix(".jxl").name, r / "p.jxl")
    common = ["--mode", "1", "--output-icc", "sRGB", "--effort", "3", "--sync",
              "--no-preflight", "--on-unknown", "convert",
              "--on-regeneration", "convert", "--on-downgrade", "convert"]
    run("red_d2", [REC, r] + common + ["--distance", "2"])
    outs = [p for p in r.rglob("*.jxl") if p.parent != r]
    rel = exif(["-s3", "-XMP-dc:Relation", outs[0]]) if outs else ""
    check("recompressor: the derivative records its distance/effort",
          len(outs) == 1 and "jxlphoto-derived:sRGB/d2e3" in rel, rel[:160])
    if outs:
        h = history(outs[0])
        check("recompressor: the last History entry of the derivative says derived",
              bool(h) and h[-1].get("Action") == "derived"
              and str(h[-1].get("SoftwareAgent", "")).startswith("jxl-photo "),
              f"{len(h)} entries")
    master_origins = origins_of(r / "p.jxl")
    origins = origins_of(outs[0]) if outs else []
    check("recompressor: the derivative keeps the master's origin",
          origins == master_origins == ["jxlphoto-origin:tiff16"],
          f"master={master_origins} derivative={origins}")
    # One provenance pair, not the master's plus its own (2026-10-11 plan): a
    # decode used to keep the master's pair, naming the TIFF, next to its own.
    tp = B / "t_pairs"
    cp(r / "p.jxl", tp / "p.jxl")
    master_src = [t for t in relation(r / "p.jxl") if t.startswith("jxlphoto-src:")]
    rc, _ = run("tr_pairs", [TR, tp, "--mode", "1", "--force-convert",
                             "--format", "jpeg"])
    jpg = tp / "recovered_jpeg" / "p.jpg"
    jrel = relation(jpg) if jpg.exists() else []
    jsrc = [t for t in jrel if t.startswith("jxlphoto-src:")]
    jsrcsum = [t for t in jrel if t.startswith("jxlphoto-srcsum:")]
    check("transcoder decode of a master: one src/srcsum pair, its own",
          rc == 0 and len(jsrc) == 1 and len(jsrcsum) == 1
          and jsrc != master_src and origins_of(jpg) == ["jxlphoto-origin:tiff16"],
          f"master={master_src} jpeg={jsrc}")
    if len(outs) != 1:
        check("recompressor: a new distance re-derives on sync", False,
              "no derivative from the first run")
        check("recompressor: --no-rederive-on-encode-change keeps the derivative",
              False, "no derivative from the first run")
        return
    rc, out = run("red_d3", [REC, r] + common + ["--distance", "3"])
    rel = exif(["-s3", "-XMP-dc:Relation", outs[0]])
    check("recompressor: a new distance re-derives on sync",
          rc == 0 and "encode settings changed" in out and "/d3e3" in rel, rel[:160])
    after3 = outs[0].stat().st_mtime_ns
    rc, _ = run("red_d4_off", [REC, r] + common + ["--distance", "4",
                                                   "--no-rederive-on-encode-change"])
    rel = exif(["-s3", "-XMP-dc:Relation", outs[0]])
    check("recompressor: --no-rederive-on-encode-change keeps the derivative",
          rc == 0 and outs[0].stat().st_mtime_ns == after3 and "/d3e3" in rel, rel[:160])
    # Same distance, LOWER effort: the existing (smaller) file is kept. The
    # last --effort on the command line wins over the one in `common`.
    rc, out = run("red_e1_kept", [REC, r] + common + ["--distance", "3",
                                                      "--effort", "1"])
    rel = exif(["-s3", "-XMP-dc:Relation", outs[0]])
    check("recompressor: a lower effort at the same distance keeps the derivative",
          rc == 0 and outs[0].stat().st_mtime_ns == after3 and "/d3e3" in rel
          and "kept: already encoded at a higher effort" in out, rel[:160])
    rc, out = run("red_e5_up", [REC, r] + common + ["--distance", "3",
                                                    "--effort", "5"])
    rel = exif(["-s3", "-XMP-dc:Relation", outs[0]])
    check("recompressor: a higher effort at the same distance re-derives",
          rc == 0 and outs[0].stat().st_mtime_ns != after3 and "/d3e5" in rel
          and "encode settings changed" in out, rel[:160])


def transcoder_checks(A1, A2):
    section("Transcoder (JPEG/PNG <-> JXL)")
    t = B / "t"
    t.mkdir(parents=True, exist_ok=True)
    _via_ascii(lambda s, o: ["magick", f"{A1}[0]", "-depth", "8", "-quality", "92", str(o)],
               None, t / "j.jpg").check_returncode()
    jpg_md5 = md5(t / "j.jpg")
    rc, _ = run("tr_d0_encode", [TR, t, t / "out", "--mode", "2", "--force-convert",
                                 "--distance", "0"])
    db = t / "out" / "checksums.md5"
    dbtxt = db.read_text(encoding="utf-8", errors="replace") if db.exists() else ""
    check("JPEG -> JXL --force-convert -d 0: run completes, checksums recorded",
          rc == 0 and (t / "out" / "j.jxl").exists() and jpg_md5 in dbtxt
          and "jxl-md5" in dbtxt, f"rc={rc}")
    origins = origins_of(t / "out" / "j.jxl")
    check("jbrd JXL (-d 0): no origin marker written (XMP would break recovery)",
          origins == [], str(origins))
    check("jbrd JXL (-d 0): no xmpMM:History entry written (same count as the JPEG)",
          _count_history(t / "out" / "j.jxl") == _count_history(t / "j.jpg"),
          f"jpeg {_count_history(t / 'j.jpg')} -> jxl {_count_history(t / 'out' / 'j.jxl')}")
    rd = B / "t_rec"
    cp(t / "out" / "j.jxl", rd / "j.jxl")
    rc, _ = run("tr_reconstruct", [TR, rd, rd / "out", "--mode", "2", "--decode",
                                   "--force-transcode"])
    recs = list((rd / "out").glob("*.jp*g"))
    check("JXL -> JPEG lossless recovery is bit-exact",
          rc == 0 and len(recs) == 1 and md5(recs[0]) == jpg_md5)
    rc, out = run("tr_delete_skipped", [TR, t, t / "out", "--mode", "2", "--force-convert",
                                        "--distance", "0", "--sync", "--delete-source",
                                        "--delete-skipped", "--delete-confirm-off"])
    check("re-run --delete-skipped: the JPEG is proven by its checksum and deleted",
          not (t / "j.jpg").exists() and "no checksum" not in out, f"rc={rc}")

    a = B / "t_auto"
    cp(rd / "out" / recs[0].name if recs else A1, a / "a.jpg")
    _via_ascii(lambda s, o: ["magick", f"{A2}[0]", "-resize", "25%", "-depth", "16", str(o)],
               None, a / "b.png").check_returncode()
    rc, _ = run("tr_auto", [TR, a, a / "out", "--mode", "2"])
    check("auto mode JPEG + PNG -> JXL completes",
          rc == 0 and (a / "out" / "a.jxl").exists() and (a / "out" / "b.jxl").exists(),
          f"rc={rc}")
    rc, _ = run("tr_png_lossy", [TR, a, a / "out_lossy", "--mode", "2", "--force-convert",
                                 "--distance", "1"])
    check("PNG/JPEG -> JXL --force-convert -d 1 completes",
          rc == 0 and (a / "out_lossy" / "b.jxl").exists(), f"rc={rc}")
    origins = origins_of(a / "out_lossy" / "a.jxl")
    check("JPEG --force-convert -d 1: origin recorded as jpeg",
          origins == ["jxlphoto-origin:jpeg"], str(origins))
    origins = origins_of(a / "out_lossy" / "b.jxl")
    check("16-bit PNG --force-convert -d 1: origin recorded as png16",
          origins == ["jxlphoto-origin:png16"], str(origins))

    # The guard counts the lossy origin as one generation (2026-10-11 plan):
    # the transcoder output has origin `jpeg` and no gen= record, so gen=1
    # after the first recompression already meets --on-regeneration.
    g1 = B / "g1"
    cp(a / "out_lossy" / "a.jxl", g1 / "a.jxl")
    run("regen_g1", [REC, g1, "--mode", "1", "--distance", "2", "--effort", "3",
                     "--no-preflight", "--no-keep-smaller",
                     "--on-unknown", "convert"])
    g1e = g1 / "recompressed_jxl" / "a.jxl"
    if not g1e.exists():
        check("a JPEG-origin file at gen=1 meets the regeneration guard", False,
              "no output from the first recompression")
        return
    cp(g1e, B / "g2" / "a.jxl")
    rc, out = run("regen_g2", [REC, B / "g2", "--mode", "1", "--distance", "3",
                               "--effort", "3", "--no-preflight",
                               "--no-keep-smaller", "--on-regeneration", "skip"])
    check("a JPEG-origin file at gen=1 meets the regeneration guard",
          not (B / "g2" / "recompressed_jxl" / "a.jxl").exists()
          and "before this toolkit" in out, f"rc={rc}")


WRAP = str(REPO / "jxl_photo.py")


def wrapper_checks(A1, A2):
    """The owner's real path: jxl_photo.py --run-preset over manifests, with
    copies of the four presets they run (SYNC PHOTOS, SRGB, MOBILE, OUTTAKES
    JPEG), on a library laid out like theirs inside the hostile folder. The
    config lives in a temporary USERPROFILE, never the owner's. Every preset
    runs twice (the second run must change nothing), then a re-export and a
    renamed event folder go through the chain again."""
    section("Wrapper presets over manifests (the owner's chain)")
    lib = B / "lib"
    ev = lib / "250118_吾妻山公園_菜の花 [FINAL]"
    tif = ev / "01_Z7" / "_EXPORT" / "TIFF16"
    a = cp(A1, tif / "DSC_0001 (2)_ProPhoto-g22_v1.tif")
    cp(A2, tif / "DSC_0002_ProPhoto-g22_v1.tif")
    cp(A2, ev / "01_Z7" / "_EXPORT" / "OLD_TIFFs" / "old_ProPhoto-g22_v1.tif")
    _via_ascii(lambda s, o: ["magick", f"{A1}[0]", "-resize", "25%", "-depth", "8",
                             "-quality", "90", str(o)],
               None, ev / "01_Z7" / "_OUTTAKES" / "DSC_0009 (1).jpg").check_returncode()

    home = B / "home [h] 1%"
    home.mkdir(parents=True, exist_ok=True)
    man = B / "manifests 設定"
    man.mkdir(parents=True, exist_ok=True)

    def _csv(name, header, row):
        p = man / name
        p.write_text("﻿" + header + "\n" + row + "\n", encoding="utf-8")
        return str(p)

    L = str(lib)
    base = {"last_input_dir": L, "last_output_mode": "99", "last_workers": 8,
            "last_staging": str(B / "staging [s]"), "last_effort": 3, "last_quality": 95,
            "last_use_ram": True, "last_overwrite_guard": False}
    presets = {
        "SYNC PHOTOS": {**base, "last_distance": 0.05, "last_origin_format": "tiff",
                        "last_dest_format": "jxl", "last_conversion_type": "jxl_tiff_encoder",
                        "last_d50_patch": "auto", "last_encode_tag": "xmp",
                        "last_multipage_mode": "split", "last_thumbnail_mode": "exclude",
                        "last_advanced_options": {"overwrite": False, "sync": True},
                        "last_expert_flags": "--restamp-then-sync",
                        "last_manifest_path": _csv(
                            "sync.csv",
                            "Source,Destination,Mode,Direction,ExcludeFolders,ExportMarker,"
                            "ExportSubfolder,ExportJxlFolder",
                            f"{L},{L},6,tiff2jxl,OLD_TIFFs;OLD_PADDED,,,")},
        "SRGB": {**base, "last_distance": 0.5, "last_origin_format": "jxl",
                 "last_dest_format": "jxl", "last_conversion_type": "jxl_recompress",
                 "last_advanced_options": {"overwrite": False, "sync": True,
                                           "on_downgrade": "copy", "on_regeneration": "convert"},
                 "last_manifest_path": _csv(
                     "srgb.csv",
                     "Source,Destination,Mode,Direction,OutputICC,Resize,Sharpen,RenameFrom,"
                     "RenameTo,ExportMarker,ExportSubfolder,ExportJxlFolder",
                     f"{L},{L},7,jxl2jxl,sRGB,,,ProPhoto-g22,sRGB,,16B_JXL,sRGB")},
        "MOBILE": {**base, "last_distance": 1.0, "last_origin_format": "jxl",
                   "last_dest_format": "jxl", "last_conversion_type": "jxl_recompress",
                   "last_advanced_options": {"overwrite": False, "sync": True,
                                             "on_downgrade": "copy", "on_regeneration": "convert"},
                   "last_manifest_path": _csv(
                       "mobile.csv",
                       "Source,Destination,Mode,Direction,OutputICC,Resize,Sharpen,RenameFrom,"
                       "RenameTo,ExportMarker,ExportSubfolder,ExportJxlFolder",
                       f"{L},{L},7,jxl2jxl,sRGB,long:2048,screen,ProPhoto-g22,sRGB,,16B_JXL,"
                       f"JXL_mobile")},
        "OUTTAKES JPEG": {**base, "last_distance": 0.05, "last_origin_format": "jpeg",
                          "last_dest_format": "jxl", "last_conversion_type": "transcode_lossless",
                          "last_advanced_options": {"overwrite": False, "sync": True},
                          "last_manifest_path": _csv(
                              "outtakes.csv",
                              "Source,Destination,Mode,Direction,ExportMarker,ExportSubfolder",
                              f"{L},{L},6,jpeg2jxl,_OUTTAKES,")},
    }
    (home / ".jxl_tools_config.json").write_text(
        json.dumps({"presets": presets, "dependencies_checked": False}, ensure_ascii=False,
                   indent=1), encoding="utf-8")
    env = {"USERPROFILE": str(home), "HOME": str(home)}

    def preset(tag, name):
        t = time.time()
        r = subprocess.run([PY, WRAP, "--run-preset", name], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=7200, cwd=str(B),
                           env={**os.environ, **env,
                                "JXLPHOTO_LOG_DIR": str(B / "_script_logs")})
        out = (r.stdout or "") + (r.stderr or "")
        (LOGS / f"{tag}.log").write_text(f"$ --run-preset {name}\nrc={r.returncode}\n\n{out}",
                                         encoding="utf-8")
        print(f"   [{tag}] rc={r.returncode} {time.time() - t:.0f}s", flush=True)
        if "Traceback (most recent call last)" in out:
            check(f"{tag}: no Python traceback", False, f"see {LOGS / (tag + '.log')}")
        return r.returncode, out

    def snapshot():
        return {str(p.relative_to(lib)): p.stat().st_mtime_ns
                for p in lib.rglob("*") if p.is_file() and p.suffix.lower() == ".jxl"}

    names = ["SYNC PHOTOS", "SRGB", "MOBILE", "OUTTAKES JPEG"]
    rcs = {n: preset(f"wr1_{n.split()[0].lower()}", n)[0] for n in names}
    first = snapshot()
    want = {"16B_JXL": 2, "sRGB": 2, "JXL_mobile": 2, "JXL_jpeg": 1}
    got = {k: sum(1 for p in first if f"\\{k}\\" in "\\" + p) for k in want}
    check("presets: masters, sRGB, mobile and the outtake JPEG written (OLD_TIFFs excluded)",
          all(v == 0 for v in rcs.values()) and got == want
          and not any("OLD_TIFFs" in p and p.endswith(".jxl") and "old" in p for p in first),
          f"rc={rcs} got={got}")
    mob = [lib / p for p in first if "\\JXL_mobile\\" in "\\" + p]
    srgb_named = [p for p in first if "\\sRGB\\" in "\\" + p]
    check("derivatives renamed ProPhoto-g22 -> sRGB, mobile at 2048 px",
          srgb_named and all("_sRGB_" in p for p in srgb_named) and mob
          and all(max(int(x) for x in exif(["-s3", "-ImageWidth", "-ImageHeight", m]).split())
                  == 2048 for m in mob), str(srgb_named[:1]))

    rcs = {n: preset(f"wr2_{n.split()[0].lower()}", n)[0] for n in names}
    check("presets run again: nothing rewritten, every run exits 0",
          snapshot() == first and all(v == 0 for v in rcs.values()), f"rc={rcs}")

    # A re-export of one photo: its master and both derivatives follow, the
    # other photo's files stay as they are.
    cp(A2, a)
    touch(a, 0)          # now: newer than its master, never in the future
    for n in names[:3]:
        preset(f"wr3_{n.split()[0].lower()}", n)
    after = snapshot()
    changed = sorted(p for p in first if after.get(p) != first[p])
    check("a re-exported TIFF: its master, sRGB and mobile files are redone, nothing else",
          len(changed) == 3 and all("DSC_0001 (2)" in p for p in changed), str(changed))

    # The event folder renamed (the owner renames in Capture One): with the
    # presets' guard off and --restamp-then-sync, nothing is re-encoded.
    renamed = lib / "250118_吾妻山公園_菜の花 Canola [FINAL]"
    ev.rename(renamed)
    base_snap = snapshot()
    rcs = {n: preset(f"wr4_{n.split()[0].lower()}", n)[0] for n in names}
    after = snapshot()
    check("renamed event folder: no master or derivative re-encoded, every run exits 0",
          after == base_snap and all(v == 0 for v in rcs.values()), f"rc={rcs}")


def _kill_when(tag, args, ready, timeout=600):
    """Start a script, kill its whole process tree (cjxl/djxl/exiftool too,
    as a power cut or the wrapper's watchdog would) once `ready()` is true."""
    t = time.time()
    p = subprocess.Popen([PY] + [str(a) for a in args], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(B),
                         env={**os.environ, "JXLPHOTO_LOG_DIR": str(B / "_script_logs")})
    while p.poll() is None and time.time() - t < timeout and not ready():
        time.sleep(0.2)
    killed = p.poll() is None
    if killed:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    p.wait()
    print(f"   [{tag}] killed={killed} after {time.time() - t:.1f}s", flush=True)
    return killed


def _decodes(p):
    return djxl(p, B / "_probe.png").returncode == 0


def interrupt_checks(A1, A2, A3):
    """Runs killed mid-write (whole process tree), then run again: no final
    file is ever truncated, no source is deleted without a verified output,
    and the next run finishes the job."""
    section("Interrupted runs (process tree killed mid-write)")
    srcs = [A1, A2, A3]

    # Encoder, mode 8 + --delete-source: killed once the first master landed.
    e = B / "k_enc 中断 [k]"
    tifs = [cp(srcs[i % 3], e / f"p{i} ({i}).tif") for i in range(6)]
    md5s = {t.name: md5(t) for t in tifs}
    ARGS = [ENC, e, "--mode", "8", "--distance", "0.05", "--effort", "7", "--workers", "2",
            "--no-preflight", "--delete-source", "--delete-confirm-off"]
    killed = _kill_when("k_enc", ARGS, lambda: len(list(e.glob("*.jxl"))) >= 1)
    finals = list(e.glob("*.jxl"))
    lost = [t.name for t in tifs if not t.exists() and not t.with_suffix(".jxl").exists()]
    bad = [j.name for j in finals if not _decodes(j)]
    check("encoder killed mid-run: every final .jxl decodes, no TIFF gone without its JXL",
          killed and not bad and not lost, f"finals={len(finals)} bad={bad} lost={lost}")
    # Masters finished before the kill are skipped by the sync; their TIFFs go
    # only with --delete-skipped, on the strength of their provenance.
    rc, _ = run("k_enc_again", ARGS + ["--delete-skipped"])
    left_tif = [t.name for t in tifs if t.exists()]
    tmps = [p.name for p in e.iterdir() if p.suffix == ".tmp"]
    check("encoder re-run (--delete-skipped): all six archived, sources deleted, no .tmp",
          rc == 0 and len(list(e.glob("*.jxl"))) == 6 and not left_tif and not tmps,
          f"rc={rc} tifs_left={left_tif} tmps={tmps}")

    # Recompressor in place (mode 8): killed after the first replacement.
    r = B / "k_rec 中断 [r]"
    for j in sorted(e.glob("*.jxl")):
        cp(j, r / j.name)
    before = {p.name: md5(p) for p in r.glob("*.jxl")}
    RARGS = [REC, r, "--mode", "8", "--distance", "1", "--effort", "7", "--workers", "2",
             "--on-unknown", "convert", "--on-regeneration", "convert", "--on-downgrade",
             "convert", "--no-preflight", "--delete-confirm-off", "--no-keep-smaller"]
    killed = _kill_when("k_rec", RARGS,
                        lambda: any(md5(r / n) != h for n, h in before.items()
                                    if (r / n).exists()))
    present = {p.name for p in r.glob("*.jxl")}
    bad = [n for n in present if not _decodes(r / n)]
    check("recompressor killed in place: every file still there and decodable",
          killed and present == set(before) and not bad, f"bad={bad} missing="
          f"{sorted(set(before) - present)}")
    rc, out = run("k_rec_again", RARGS)
    tmps = [p.name for p in r.iterdir() if p.suffix == ".tmp"]
    check("recompressor re-run: finishes, no .tmp left behind",
          rc == 0 and not tmps and all(_decodes(p) for p in r.glob("*.jxl")),
          f"rc={rc} tmps={tmps}")

    # Transcoder mode 8 + --delete-source on JPEGs: killed after the first JXL.
    t = B / "k_tr 中断 [t]"
    for i, s in enumerate(srcs * 3):
        _via_ascii(lambda _s, o, s=s: ["magick", f"{s}[0]", "-depth", "8", "-quality", "95",
                                       str(o)], None, t / f"j{i} ({i}).jpg").check_returncode()
    jmd5 = {p.stem: md5(p) for p in t.glob("*.jpg")}
    TARGS = [TR, t, "--mode", "8", "--delete-source", "--delete-confirm-off", "--workers", "2"]
    killed = _kill_when("k_tr", TARGS, lambda: len(list(t.glob("*.jxl"))) >= 1)
    lost = [s for s in jmd5 if not (t / f"{s}.jpg").exists() and not (t / f"{s}.jxl").exists()]
    check("transcoder killed mid-run: no JPEG gone without its JXL",
          not lost, f"killed={killed} lost={lost}")
    rc, _ = run("k_tr_again", TARGS + ["--delete-skipped"])
    rd = B / "k_tr_rec"
    for p in t.glob("*.jxl"):
        cp(p, rd / p.name)
    run("k_tr_reconstruct", [TR, rd, "--mode", "1", "--decode", "--force-transcode"])
    recs = {p.stem: md5(p) for p in rd.rglob("*.jpg")}
    check("transcoder re-run (--delete-skipped): every JPEG archived, bit-exact, deleted",
          rc == 0 and recs == jmd5 and not list(t.glob("*.jpg")),
          f"rc={rc} {len(recs)}/{len(jmd5)}")


def interaction_checks(A1, A2):
    """Features added after the 2026-10-08 audit, combined, each run fed its
    own output: the restamp with an edited photo, the guard switched off
    between runs, a master decoded and re-encoded (origin, lineage, History,
    provenance), and the regeneration guard on that second generation."""
    section("Feature interactions (runs fed their own output)")
    FAST8 = ["--mode", "8", "--distance", "1", "--effort", "3", "--no-preflight"]

    # (a) Folder renamed, then ONE of its two photos re-exported with other
    # pixels. --restamp-then-sync must restamp the untouched master without
    # re-encoding it and refuse the edited one (guard on); with the guard
    # off the edited one is re-encoded and the untouched one still kept.
    root = B / "ix_restamp"
    g = root / "2024-05 Viagem"
    cp(A1, g / "keep.tif")
    cp(A1, g / "edit.tif")
    run("ix_encode", [ENC, root] + FAST8)
    g.rename(root / "2024-05 京都 [Kyoto]")
    g = root / "2024-05 京都 [Kyoto]"
    keep, edit = g / "keep.jxl", g / "edit.jxl"
    k_mtime, k_hist = keep.stat().st_mtime_ns, _count_history(keep)
    cp(A2, g / "edit.tif")
    touch(g / "edit.tif", 60)
    e_md5 = md5(edit)
    rc, out = run("ix_restamp_sync", [ENC, root] + FAST8 + ["--restamp-then-sync"])
    check("restamp-then-sync: untouched master restamped, not re-encoded",
          keep.stat().st_mtime_ns == k_mtime and _count_history(keep) == k_hist
          and "RESTAMP |" in out, f"rc={rc}")
    check("restamp-then-sync: the re-exported photo is refused (guard on), master kept",
          md5(edit) == e_md5 and rc != 0, f"rc={rc}")
    rc, out = run("ix_restamp_sync_again", [ENC, root] + FAST8 + ["--restamp-then-sync"])
    check("restamp-then-sync run again: nothing restamped twice",
          keep.stat().st_mtime_ns == k_mtime and "RESTAMP |" not in out, f"rc={rc}")
    rc, out = run("ix_guard_off", [ENC, root] + FAST8 + ["--restamp-then-sync",
                                                         "--no-overwrite-guard"])
    check("guard off: the re-exported photo is re-encoded, the untouched one kept",
          rc == 0 and md5(edit) != e_md5 and keep.stat().st_mtime_ns == k_mtime
          and _count_history(edit) == k_hist, f"rc={rc}")
    rc, _ = run("ix_restamp_delete", [ENC, root] + FAST8 + ["--restamp-then-sync",
                                                            "--delete-source",
                                                            "--delete-confirm-off"])
    check("restamp-then-sync with --delete-source is refused, nothing deleted",
          rc == 2 and (g / "keep.tif").exists() and (g / "edit.tif").exists(), f"rc={rc}")

    # (b) master -> decode -> re-encode: a second lossy generation.
    m = B / "ix_gen"
    cp(A1, m / "p.tif")
    run("ix_gen1", [ENC, m, "--mode", "1"] + FAST8[2:])
    j1 = m / "converted_jxl" / "p.jxl"
    d = B / "ix_gen_dec"
    if j1.exists():
        cp(j1, d / "p.jxl")
    run("ix_gen_decode", [DEC, d, "--mode", "1"])
    t2 = d / "converted_tiff" / "p.tif"
    e2 = B / "ix_gen2"
    if t2.exists():
        cp(t2, e2 / "p.tif")
    rc, _ = run("ix_gen2", [ENC, e2, "--mode", "1"] + FAST8[2:])
    j2 = e2 / "converted_jxl" / "p.jxl"
    desc = exif(["-s3", "-XMP-dc:Description", j2]) if j2.exists() else ""
    src2 = [x for x in relation(j2) if x.startswith("jxlphoto-src:")] if j2.exists() else []
    src1 = [x for x in relation(j1) if x.startswith("jxlphoto-src:")] if j1.exists() else []
    check("re-encoded decode: gen=2 lineage with both encode entries",
          rc == 0 and "gen=2" in desc and desc.count("cjxl d=") == 2, desc[:120])
    check("re-encoded decode: origin still tiff16, one src pair naming the decoded TIFF",
          j2.exists() and origins_of(j2) == ["jxlphoto-origin:tiff16"]
          and len(src2) == 1 and src2 != src1, f"{origins_of(j2) if j2.exists() else []}")
    check("re-encoded decode: History = master 1 + decode 1 + encode 1",
          j2.exists() and _count_history(j2) == 3,
          f"{_count_history(j2) if j2.exists() else 'no output'} entries")
    g2 = B / "ix_gen_rec"
    if j2.exists():
        cp(j2, g2 / "p.jxl")
    rc, out = run("ix_gen_regen", [REC, g2, "--mode", "1", "--distance", "2", "--effort", "3",
                                   "--no-preflight", "--on-regeneration", "skip"])
    check("recompressor: a gen=2 master meets the regeneration guard (skipped)",
          not list((g2).rglob("recompressed_jxl/*.jxl")) and rc == 0, f"rc={rc}")


def varied_checks(varied):
    """Every TIFF in <fixtures>/TIFF16_varied (cameras, phones, a film
    scanner, ICC v2/v4, ISO 64-8000, portrait, odd sizes, smooth to noisy)
    through encode -> decode: ICC back byte for byte, colours close to the
    master, and the camera EXIF carried to the JXL and back to the TIFF."""
    section("Varied exports (TIFF16_varied)")
    srcs = sorted(varied.glob("*.tif"))
    v = B / "varied"
    for s in srcs:
        cp(s, v / s.name)
    rc, _ = run("var_encode", [ENC, v, "--mode", "1", "--distance", "0.1", "--effort", "3",
                               "--no-preflight"])
    jxls = sorted((v / "converted_jxl").glob("*.jxl"))
    check(f"all {len(srcs)} varied TIFFs encoded", rc == 0 and len(jxls) == len(srcs),
          f"rc={rc} {len(jxls)}/{len(srcs)}")
    vd = B / "varied_dec"
    for j in jxls:
        cp(j, vd / j.name)
    rc, _ = run("var_decode", [DEC, vd, "--mode", "1"])
    tags = ["-Make", "-Model", "-DateTimeOriginal", "-ISO", "-LensModel"]

    def _exif_rows(files):
        try:
            rows = json.loads(exif(["-j"] + tags + list(files)))
        except Exception:
            return {}
        return {Path(r["SourceFile"]).stem: {t[1:]: r.get(t[1:]) for t in tags} for r in rows}

    e_src = _exif_rows(v / s.name for s in srcs)
    e_jxl = _exif_rows(jxls)
    e_tif = _exif_rows(sorted((vd / "converted_tiff").glob("*.tif")))
    bad_icc, bad_exif, worst = [], [], (999.0, "")
    ratios = []
    for s in srcs:
        t = vd / "converted_tiff" / s.name
        j = v / "converted_jxl" / s.with_suffix(".jxl").name
        if not t.exists() or not j.exists():
            bad_icc.append(s.name + " (missing)")
            continue
        ratios.append(j.stat().st_size / s.stat().st_size)
        with tifffile.TiffFile(s) as t0, tifffile.TiffFile(t) as t1:
            i0, i1 = t0.pages[0].tags.get(34675), t1.pages[0].tags.get(34675)
            if i0 is None or i1 is None or i0.value != i1.value:
                bad_icc.append(s.name)
            x, y = t0.pages[0].asarray(), t1.pages[0].asarray()
        if x.shape != y.shape:
            bad_icc.append(s.name + " (shape)")
            continue
        sse = 0.0
        for i in range(0, x.shape[0], 256):
            d = x[i:i + 256].astype(np.float64) - y[i:i + 256].astype(np.float64)
            sse += float((d * d).sum())
        mse = sse / x.size
        psnr = float("inf") if mse == 0 else 10 * np.log10(65535.0 ** 2 / mse)
        worst = min(worst, (psnr, s.name))
        st = s.stem
        if not (e_src.get(st) == e_jxl.get(st) == e_tif.get(st)):
            bad_exif.append(st)
    check("varied: decode restores every original ICC byte for byte",
          rc == 0 and not bad_icc, f"rc={rc} bad={bad_icc[:4]}")
    check("varied: every decode within 40 dB of its master (d=0.1)", worst[0] >= 40.0,
          f"worst {worst[0]:.1f} dB ({worst[1]})")
    check("varied: Make/Model/date/ISO/lens carried to the JXL and back",
          not bad_exif and len(e_src) == len(srcs), f"differ={bad_exif[:4]}")
    if ratios:
        check("varied: size ratio JXL/TIFF at d=0.1 (information)", True,
              f"{min(ratios):.1%}-{max(ratios):.1%}")


def scan_checks(scans):
    """Every TIFF in <fixtures>/TIFF_scans_varied (8/16-bit gray and RGB,
    LZW/Deflate/none, no ICC, scanner and EPSON gamma-1.8 profiles, linear
    scans, RGB+IR and alpha as a 4th sample, 84 MP) through the encoder and
    decoder: lossless must come back bit for bit with the same ICC (or none),
    lossy must keep its colours."""
    section("Varied scans (TIFF_scans_varied)")
    srcs = sorted(p for p in scans.iterdir() if p.suffix.lower() in (".tif", ".tiff"))

    def _page0(p):
        with tifffile.TiffFile(p) as t:
            pg = t.pages[0]
            icc = pg.tags[34675].value if 34675 in pg.tags else None
            return pg.asarray(), icc

    for label, dist in (("lossless", "0"), ("lossy d=1", "1")):
        tag = "0" if dist == "0" else "1"
        e = B / f"scan_d{tag}"
        for s in srcs:
            cp(s, e / s.name)
        # --scan-distance same: these checks are about the CODEC path at this
        # distance; since v2.17.0 a scan defaults to lossless (checked below).
        rc, out = run(f"scan_enc_d{tag}", [ENC, e, "--mode", "1", "--distance", dist,
                                          "--effort", "2", "--no-preflight",
                                          "--scan-distance", "same"])
        jx = {p.stem: p for p in (e / "converted_jxl").glob("*.jxl")}
        missing = [s.name for s in srcs if s.stem not in jx]
        check(f"scans {label}: every scan encoded", rc == 0 and not missing,
              f"rc={rc} missing={missing[:4]}")
        dd = B / f"scan_d{tag}_dec"
        for p in jx.values():
            cp(p, dd / p.name)
        # preserve_original: the default (preserve_thumbnails) writes real
        # pages at 16 bits by design, and this check wants the 8-bit scans
        # back as 8-bit.
        rc, _ = run(f"scan_dec_d{tag}", [DEC, dd, "--mode", "1",
                                         "--depth-policy", "preserve_original"])
        bad, psnrs, shifts = [], [], []
        for s in srcs:
            t = dd / "converted_tiff" / (s.stem + ".tif")
            if not t.exists():
                if s.stem in jx:
                    bad.append(f"{s.stem}: no decode")
                continue
            a, icc_a = _page0(s)
            b, icc_b = _page0(t)
            if icc_a != icc_b:
                bad.append(f"{s.stem}: ICC {'lost' if icc_a else 'added'}"
                           if (icc_a is None) != (icc_b is None) else f"{s.stem}: ICC changed")
            if a.shape != b.shape or a.dtype != b.dtype:
                bad.append(f"{s.stem}: {a.shape}/{a.dtype} -> {b.shape}/{b.dtype}")
                continue
            if dist == "0":
                if not np.array_equal(a, b):
                    bad.append(f"{s.stem}: pixels differ")
                continue
            peak = 255.0 if a.dtype == np.uint8 else 65535.0
            sse = 0.0
            for i in range(0, a.shape[0], 256):
                d = a[i:i + 256].astype(np.float64) - b[i:i + 256].astype(np.float64)
                sse += float((d * d).sum())
            mse = sse / a.size
            psnr = float("inf") if mse == 0 else 10 * np.log10(peak ** 2 / mse)
            psnrs.append((psnr, s.stem))
            # Codec error is noise and averages out; a wrong profile moves
            # the whole image.
            ax = tuple(range(a.ndim - 1)) if a.ndim == 3 else None
            shift = np.abs(a.mean(axis=ax, dtype=np.float64)
                           - b.mean(axis=ax, dtype=np.float64)).max() / peak
            shifts.append((float(shift), s.stem))
        if dist == "0":
            check("scans lossless: bit-exact round trip, same shape/depth/ICC",
                  rc == 0 and not bad, f"rc={rc} {bad[:5]}")
        else:
            check("scans lossy: same shape/depth/ICC after the round trip",
                  rc == 0 and not bad, f"rc={rc} {bad[:5]}")
            # 28 dB, not the exports' 40: a grainy 8-bit film scan at d=1 is
            # ~30 dB with plain cjxl/djxl too (measured 30.3 vs the toolkit's
            # 30.5 on konica400-4, 2026-10-22). A colour bug lands far lower
            # (the 2026-10-08 scanner-profile bug: 27 dB at d=2, 18 dB later).
            psnrs.sort()
            check("scans lossy d=1: every decode within 28 dB of the scan",
                  bool(psnrs) and psnrs[0][0] >= 28.0,
                  "worst " + ", ".join(f"{v:.1f} dB ({n})" for v, n in psnrs[:3]))
            shifts.sort(reverse=True)
            check("scans lossy d=1: no colour shift (channel means within 0.5% of full scale)",
                  bool(shifts) and shifts[0][0] <= 0.005,
                  "largest " + ", ".join(f"{v:.3%} ({n})" for v, n in shifts[:2]))

    # v2.17.0: with the defaults a film scan is archived LOSSLESS even in a
    # lossy run (SCAN_DISTANCE = 0), and marked as a scan. The three smallest
    # scans whose Software names a scanner program, at --distance 1.
    def _software(p):
        with tifffile.TiffFile(p) as t:
            tag = t.pages[0].tags.get(305)
            return str(tag.value) if tag is not None else ""
    scanner = sorted((s for s in srcs
                      if any(n in _software(s) for n in ("SilverFast", "VueScan"))),
                     key=lambda p: p.stat().st_size)[:3]
    e = B / "scan_default"
    for s in scanner:
        cp(s, e / s.name)
    rc, out = run("scan_default", [ENC, e, "--mode", "1", "--distance", "1",
                                    "--effort", "2", "--no-preflight"])
    wrong = []
    for s in scanner:
        j = e / "converted_jxl" / (s.stem + ".jxl")
        desc = exif(["-s3", "-XMP-dc:Description", j]) if j.exists() else ""
        rel = exif(["-s3", "-XMP-dc:Relation", j]) if j.exists() else ""
        if not desc.strip().endswith("cjxl d=0.0 e=2") or "jxlphoto-scan:" not in rel:
            wrong.append(s.name)
    check("scans at --distance 1 with the defaults: archived lossless and marked",
          rc == 0 and bool(scanner) and not wrong, f"rc={rc} {len(scanner)} scans, wrong={wrong}")


def _lsi_xmp(path):
    """SilverFast's own XMP fields (namespace .../LSI/...) of a file, as a set
    of (name, value): attributes and simple elements of every LSI prefix (a TIFF)."""
    with tifffile.TiffFile(path) as t:
        tag = t.pages[0].tags.get(700)
        raw = bytes(tag.value).decode("utf-8", "replace") if tag is not None else ""
    # Attributes in SilverFast's packet, elements (and ' quotes) once exiftool
    # has rewritten it: compare names and unescaped values, not the layout.
    prefixes = set(re.findall(r'xmlns:(\w+)=["\'][^"\']*LSI/[^"\']*["\']', raw))
    out = set()
    for pfx in prefixes:
        out |= {(n, html.unescape(v)) for n, v in re.findall(rf'\b({pfx}:\w+)="([^"]*)"', raw)}
        out |= {(n, html.unescape(v))
                for n, v in re.findall(rf'<({pfx}:\w+)>([^<]*)</{pfx}:\w+>', raw)}
    return out


def silverfast_checks(folder):
    """<fixtures>/silverfast_xmp: original SilverFast scans (image, thumbnail,
    IR; SilverFast's own XMP namespace LSI/ and its private TIFF tags), the
    smallest one through the encoder with the defaults at --distance 1 and back
    through the decoder. SilverFast recognises the IR channel and shows the
    icon only with all of it (v2.17.0): archived lossless, every page and
    SubfileType back bit for bit, the LSI fields and the private tags (up to
    the encoder's 4 KB) unchanged."""
    section("SilverFast originals (silverfast_xmp)")
    srcs = sorted((p for p in folder.iterdir() if p.suffix.lower() in (".tif", ".tiff")),
                  key=lambda p: p.stat().st_size)
    if not srcs:
        print("(silverfast_xmp/ is empty)")
        return
    src = srcs[0]
    e = B / "sf"
    cp(src, e / src.name)
    rc, _ = run("sf_enc", [ENC, e, "--mode", "1", "--distance", "1", "--effort", "2",
                           "--no-preflight"])
    jxls = sorted((e / "converted_jxl").glob("*.jxl"))
    main_jxl = e / "converted_jxl" / (src.stem + ".jxl")
    desc = exif(["-s3", "-XMP-dc:Description", main_jxl]) if main_jxl.exists() else ""
    check("SilverFast scan: archived lossless at --distance 1, every page",
          rc == 0 and desc.strip().endswith("cjxl d=0.0 e=2") and len(jxls) == 3,
          f"rc={rc} {[p.name for p in jxls]} {desc.strip()[-20:]!r}")
    d = B / "sf_dec"
    for p in jxls:
        cp(p, d / p.name)
    rc, _ = run("sf_dec", [DEC, d, "--mode", "1", "--depth-policy", "preserve_original"])
    out = d / "converted_tiff" / (src.stem + ".tif")
    if not out.exists():
        check("SilverFast scan: decoded back", False, f"rc={rc} no {out.name}")
        return
    bad = []
    with tifffile.TiffFile(src) as ta, tifffile.TiffFile(out) as tb:
        if len(ta.pages) != len(tb.pages):
            bad.append(f"{len(ta.pages)} -> {len(tb.pages)} pages")
        for i, (pa, pb) in enumerate(zip(ta.pages, tb.pages)):
            if int(pa.subfiletype or 0) != int(pb.subfiletype or 0):
                bad.append(f"page {i}: SubfileType {pa.subfiletype} -> {pb.subfiletype}")
            if not np.array_equal(pa.asarray(), pb.asarray()):
                bad.append(f"page {i}: pixels differ")
        priv_a = {c: bytes(v.value) if isinstance(v.value, (bytes, bytearray)) else v.value
                  for c, v in ta.pages[0].tags.items() if c in (35243, 35244)}
        priv_b = {c: bytes(v.value) if isinstance(v.value, (bytes, bytearray)) else v.value
                  for c, v in tb.pages[0].tags.items() if c in (35243, 35244)}
        small = {c for c, v in priv_a.items() if len(v if isinstance(v, bytes) else str(v)) <= 4096}
    check("SilverFast scan: every page, SubfileType and pixel back", rc == 0 and not bad,
          f"rc={rc} {bad[:4]}")
    lost = [c for c in sorted(small) if priv_b.get(c) != priv_a[c]]
    check("SilverFast scan: its private TIFF tags come back byte for byte",
          bool(small) and not lost, f"tags {sorted(small)} lost/changed={lost}")
    lsi_a, lsi_b = _lsi_xmp(src), _lsi_xmp(out)
    check("SilverFast scan: SilverFast's own XMP (LSI/) comes back unchanged",
          bool(lsi_a) and lsi_a == lsi_b,
          f"{len(lsi_a)} fields, missing={sorted(lsi_a - lsi_b)[:3]}")


def tagless_checks(folder):
    """<fixtures>/TIFF_tagless/<camera>/*.tif: camera TIFFs that lost every
    tag (a Fuji S5 Pro and a Nikon E990 TIFF rewritten without Software, Make
    or EXIF: the image plus the 160x120 thumbnail page cameras write) and an
    E990 TIFF as the camera wrote it. None is a film scan: at --distance 1
    each is encoded at 1, the tagless ones marked jxlphoto-scan:no, and the
    run does not announce a guessed scan (v2.17.0)."""
    section("Camera TIFFs without tags (TIFF_tagless)")
    srcs = sorted(p for p in folder.rglob("*") if p.suffix.lower() in (".tif", ".tiff"))
    e = B / "tagless"
    for s in srcs:
        cp(s, e / s.parent.name / s.name)
    rc, out = run("tagless_enc", [ENC, e, "--mode", "8", "--distance", "1", "--effort", "2",
                                  "--no-preflight"])
    wrong = []
    for s in srcs:
        j = e / s.parent.name / (s.stem + ".jxl")
        if not j.exists():
            wrong.append(f"{s.name}: no JXL")
            continue
        desc = exif(["-s3", "-XMP-dc:Description", j]).strip()
        rel = relation(j)
        with tifffile.TiffFile(s) as t:
            tagless = 305 not in t.pages[0].tags and 34665 not in t.pages[0].tags
        want = ["jxlphoto-scan:no"] if tagless else []
        got = [r for r in rel if r.startswith("jxlphoto-scan:")]
        if not desc.endswith("cjxl d=1.0 e=2") or got != want:
            wrong.append(f"{s.name}: {desc[-16:]!r} {got}")
    check("camera TIFFs without tags: not film scans (encoded at --distance, tagless "
          "ones marked jxlphoto-scan:no)",
          rc == 0 and bool(srcs) and not wrong, f"rc={rc} {len(srcs)} TIFFs, wrong={wrong}")
    check("camera TIFFs without tags: no guessed-scan warning",
          "only a guess" not in out, "")


def jpeg_checks(jpegs):
    """Every file in <fixtures>/JPEG_varied (baseline/progressive, 4:2:0 to
    4:4:4 and 4:4:0, gray, MPF trailers, P3/Adobe/odd ICCs, broken
    MakerNotes, plus an EMPTY file and a macOS '._' file named .jpg) through
    the transcoder: valid JPEGs come back bit for bit, invalid ones are
    refused without a traceback, and the deleting mode removes only what it
    proved -- twice, the second run fed its own output."""
    section("Varied JPEGs (JPEG_varied)")
    srcs = sorted(p for p in jpegs.iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))
    invalid = {p.name for p in srcs if p.stat().st_size < 1024}
    valid = [p for p in srcs if p.name not in invalid]
    j = B / "jpg"
    for s in srcs:
        cp(s, j / s.name)
    rc, out = run("jpg_encode", [TR, j, "--mode", "1"])
    made = {p.stem for p in (j / "converted_jxl").glob("*.jxl")}
    missing = [s.name for s in valid if s.stem not in made]
    check("JPEGs: every valid JPEG transcoded losslessly",
          not missing, f"rc={rc} missing={missing[:4]}")
    check("JPEGs: the empty and the non-JPEG file produce no output",
          not any(Path(n).stem in made for n in invalid), f"invalid={sorted(invalid)}")
    rj = B / "jpg_rec"
    for p in (j / "converted_jxl").glob("*.jxl"):
        cp(p, rj / p.name)
    rc, _ = run("jpg_reconstruct", [TR, rj, "--mode", "1", "--decode", "--force-transcode"])
    recs = {p.stem: p for p in rj.rglob("*.jp*g")}
    bad = [s.name for s in valid if s.stem in made
           and (s.stem not in recs or md5(recs[s.stem]) != md5(s))]
    check("JPEGs: every transcoded JPEG reconstructs bit for bit", rc == 0 and not bad,
          f"rc={rc} bad={bad[:4]}")

    d = B / "jpg_del"
    for s in srcs:
        cp(s, d / s.name)
    ARGS = [TR, d, "--mode", "8", "--delete-source", "--delete-confirm-off"]
    rc, _ = run("jpg_delete", ARGS)
    left = {p.name for p in d.glob("*") if p.suffix.lower() in (".jpg", ".jpeg")}
    jx = {p.stem for p in d.glob("*.jxl")}
    # A read-only JPEG (files rescued from a CD keep the attribute) cannot be
    # deleted: kept next to its JXL is the right outcome for it.
    ro = {n for n in left if not os.access(d / n, os.W_OK)}
    wrong = [s.name for s in valid if (s.name in left) == (s.stem in jx)
             and not (s.name in ro and s.name in left)]
    check("mode 8: a JPEG is deleted exactly when its JXL was written (read-only kept)",
          not wrong and invalid <= left,
          f"rc={rc} wrong={wrong[:4]} read-only={sorted(ro)[:3]}")
    rc, out = run("jpg_delete_again", ARGS)
    left2 = {p.name for p in d.glob("*") if p.suffix.lower() in (".jpg", ".jpeg")}
    check("mode 8 run again over its own output: nothing else deleted, no new JXL",
          left2 == left and {p.stem for p in d.glob("*.jxl")} == jx, f"rc={rc}")


def _thumb_box(path):
    """The battery's own reading of the thumbnail box the scripts store
    (EXPLORER_THUMBNAIL), written independently of theirs: (recorded
    fingerprint, actual fingerprint of the codestream, JPEG bytes), or None
    when the file has none. Fingerprint = codestream length + FNV-1a 64 of
    its first 64 KiB."""
    import struct
    data = Path(path).read_bytes()
    pos, cs, rec = 0, [], None
    while pos + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", data[pos + 8:pos + 16])[0], 16
        elif size == 0:
            size = len(data) - pos
        body = data[pos + hdr:pos + size]
        if typ == b"jxlc":
            cs.append(body)
        elif typ == b"jxlp":
            cs.append(body[4:])
        elif typ == b"uuid" and body[:16] == bytes.fromhex("4e0b4016f51c473d9669bc35aebe3c16"):
            line, jpeg = body[16:].split(b"\n", 1)
            rec = (line.decode().removeprefix("jxlphoto-thumbnail:v1:"), jpeg)
        pos += size
    if rec is None:
        return None
    stream = b"".join(cs)
    h = 0xcbf29ce484222325
    for b in stream[:65536]:
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return rec[0], f"{len(stream)}:{h:016x}", rec[1]


def explorer_thumbnail_checks(A1, pool_jxl):
    """Explorer thumbnails on a real photo: stored in a lossless master, its
    own box keeps djxl's JPEG output working (an EXIF one would not fit a
    JPEG's 64 KB segment), renewed in place on an existing lossy master with
    its modified date kept, and never carried into a JPEG or TIFF."""
    section("Explorer thumbnails")
    import io
    from PIL import Image
    t = B / "t_enc"
    cp(A1, t / A1.name)
    rc, _ = run("t_enc_lossless", [ENC, t, "--mode", "1", "--distance", "0",
                                   "--effort", "1", "--no-preflight"])
    jxl = t / "converted_jxl" / A1.with_suffix(".jxl").name
    tb = _thumb_box(jxl) if jxl.exists() else None
    size = Image.open(io.BytesIO(tb[2])).size if tb else None
    check("encoder: a lossless master carries a 768 px thumbnail whose fingerprint "
          "names its codestream",
          rc == 0 and tb is not None and tb[0] == tb[1] and max(size) == 768,
          f"rc={rc} box={tb[:2] if tb else None} size={size}")
    if jxl.exists():
        r = djxl(jxl, B / "t_djxl.jpg")
        err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else (r.stderr or "")
        check("djxl writes a JPEG from it (the thumbnail is not in the EXIF)",
              r.returncode == 0, err.strip()[-200:])

    a2 = B / "t_auto"
    cp(A1, a2 / A1.name)
    os.environ["JXLPHOTO_JXL_THUMB"] = "1"
    try:
        rc, out = run("t_enc_auto_installed", [ENC, a2, "--mode", "1", "--distance", "1",
                                               "--effort", "3", "--no-preflight"])
    finally:
        os.environ["JXLPHOTO_JXL_THUMB"] = "0"
    lj = a2 / "converted_jxl" / A1.with_suffix(".jxl").name
    tb = _thumb_box(lj) if lj.exists() else None
    check("encoder: with jxl-thumb installed, auto gives a lossy master one too",
          rc == 0 and tb is not None and tb[0] == tb[1] and "jxl-thumb is installed" in out,
          f"rc={rc}")

    r = B / "t_refresh"
    lossy = next(iter(sorted(pool_jxl.glob("*.jxl"))), None)
    if lossy is not None:
        cp(lossy, r / lossy.name)
        target = r / lossy.name
        old = 1_600_000_000
        os.utime(target, (old, old))
        rc, out = run("t_refresh", [REC, r, "--mode", "0", "--refresh-explorer-thumbnails",
                                    "--explorer-thumbnail", "on"])
        tb = _thumb_box(target)
        check("recompressor --refresh-explorer-thumbnails: a lossy master gains a valid "
              "thumbnail and keeps its modified date",
              rc == 0 and tb is not None and tb[0] == tb[1]
              and int(target.stat().st_mtime) == old, f"rc={rc}")
        rc, out = run("t_refresh_again", [REC, r, "--mode", "0",
                                          "--refresh-explorer-thumbnails",
                                          "--explorer-thumbnail", "on"])
        check("a second refresh finds it current and writes nothing",
              rc == 0 and "1 already current" in out, f"rc={rc}")

    if jxl.exists():
        rc, out = run("t_to_jpeg", [TR, jxl.parent, "--mode", "1", "--from-jxl",
                                    "--format", "jpeg"])
        jpgs = list(jxl.parent.rglob(A1.with_suffix(".jpg").name))
        check("transcoder: the master decodes to JPEG, which carries no thumbnail box",
              rc == 0 and jpgs and b"jxlphoto-thumbnail" not in jpgs[0].read_bytes(),
              f"rc={rc}")


def hostile_name_checks(A1):
    """The four scripts on a FILE name as hostile as the folder (the folder of
    every other check already is): Japanese, [ ], parentheses, a space and %,
    then a path longer than 260 characters."""
    section("Hostile file names and long paths")
    name = "DSC_0001 (2) 菜の花 [v2] 5%"
    FAST = ["--distance", "1", "--effort", "3", "--no-preflight"]
    with tifffile.TiffFile(A1) as t:
        icc0 = t.pages[0].tags[34675].value if 34675 in t.pages[0].tags else None

    h = B / "h_enc"
    cp(A1, h / f"{name}.tif")
    rc, _ = run("h_enc", [ENC, h, "--mode", "1"] + FAST)
    jxl = h / "converted_jxl" / f"{name}.jxl"
    src = [x for x in relation(jxl) if x.startswith("jxlphoto-src:")] if jxl.exists() else []
    check("encoder: hostile file name encoded, provenance recorded",
          rc == 0 and jxl.exists() and len(src) == 1
          and djxl(jxl, B / "h_enc.png").returncode == 0, f"rc={rc}")
    if not jxl.exists():
        return
    # #550: the name ends in '5%', which exiftool expanded in -tagsFromFile:
    # the JXL carried none of the TIFF's EXIF (only the XMP), silently.
    exp_src = exif(["-s3", "-EXIF:ExposureTime", h / f"{name}.tif"]).strip()
    exp_jxl = exif(["-s3", "-EXIF:ExposureTime", jxl]).strip()
    check("encoder: the hostile file name keeps its EXIF (a '%' in the name, #550)",
          bool(exp_src) and exp_jxl == exp_src, f"source {exp_src!r} -> JXL {exp_jxl!r}")
    mtime = jxl.stat().st_mtime_ns
    rc, out = run("h_enc_again", [ENC, h, "--mode", "1"] + FAST)
    check("encoder: a second run over its own output skips it (smart sync)",
          rc == 0 and jxl.stat().st_mtime_ns == mtime, f"rc={rc}")

    d = B / "h_dec"
    cp(jxl, d / f"{name}.jxl")
    rc, _ = run("h_dec", [DEC, d, "--mode", "1"])
    tif = d / "converted_tiff" / f"{name}.tif"
    icc1 = None
    if tif.exists():
        with tifffile.TiffFile(tif) as t:
            icc1 = t.pages[0].tags[34675].value if 34675 in t.pages[0].tags else None
    check("decoder: hostile file name decoded, original ICC restored",
          rc == 0 and tif.exists() and icc0 is not None and icc1 == icc0, f"rc={rc}")

    r = B / "h_rec"
    cp(jxl, r / f"{name}.jxl")
    before = md5(r / f"{name}.jxl")
    rc, _ = run("h_rec_inplace", [REC, r, "--mode", "8", "--distance", "2", "--effort", "3",
                                  "--on-unknown", "convert", "--on-regeneration", "convert",
                                  "--on-downgrade", "convert", "--no-preflight",
                                  "--delete-confirm-off"])
    check("recompressor: hostile file name replaced in place, no .tmp left",
          rc == 0 and md5(r / f"{name}.jxl") != before and not list(r.glob("*.tmp")),
          f"rc={rc}")
    rc, _ = run("h_rec_deriv", [REC, r, "--mode", "1", "--output-icc", "sRGB",
                                "--distance", "2", "--effort", "3", "--no-preflight",
                                "--on-unknown", "convert", "--on-regeneration", "convert",
                                "--on-downgrade", "convert"])
    outs = [p for p in r.rglob("*.jxl") if p.parent != r]
    check("recompressor: sRGB derivative of a hostile file name",
          rc == 0 and len(outs) == 1 and "jxlphoto-derived:sRGB" in exif(
              ["-s3", "-XMP-dc:Relation", outs[0]]), f"rc={rc} outs={len(outs)}")

    t = B / "h_tr"
    _via_ascii(lambda s, o: ["magick", f"{A1}[0]", "-depth", "8", "-quality", "92", str(o)],
               None, t / f"{name}.jpg").check_returncode()
    jmd5 = md5(t / f"{name}.jpg")
    rc, _ = run("h_tr_encode", [TR, t, "--mode", "1", "--force-convert", "--distance", "0"])
    jx = t / "converted_jxl" / f"{name}.jxl"
    rd = B / "h_tr_rec"
    if jx.exists():
        cp(jx, rd / f"{name}.jxl")
    rc2, _ = run("h_tr_reconstruct", [TR, rd, "--mode", "1", "--decode", "--force-transcode"])
    recs = [p for p in rd.rglob("*.jp*g")]
    check("transcoder: hostile JPEG -> JXL -> JPEG is bit-exact",
          rc == 0 and rc2 == 0 and len(recs) == 1 and md5(recs[0]) == jmd5,
          f"rc={rc}/{rc2} recs={len(recs)}")

    # Past MAX_PATH: the file's full path is over 260 characters.
    deep = B / "h_long"
    while len(str(deep / "sub" / f"{name}.tif")) <= 270:
        deep = deep / "長いフォルダ名 [long] folder"
    cp(A1, deep / f"{name}.tif")
    rc, _ = run("h_long_enc", [ENC, deep, "--mode", "1"] + FAST)
    lj = deep / "converted_jxl" / f"{name}.jxl"
    check(f"encoder: a source path of {len(str(deep / (name + '.tif')))} characters",
          rc == 0 and lj.exists(), f"rc={rc}")
    if lj.exists():
        ld = deep / "dec"
        cp(lj, ld / f"{name}.jxl")
        rc, _ = run("h_long_dec", [DEC, ld, "--mode", "1"])
        check("decoder: the same long path decodes",
              rc == 0 and (ld / "converted_tiff" / f"{name}.tif").exists(), f"rc={rc}")
        lr = deep / "rec"
        cp(lj, lr / f"{name}.jxl")
        before = md5(lr / f"{name}.jxl")
        rc, _ = run("h_long_rec", [REC, lr, "--mode", "8", "--distance", "2", "--effort", "3",
                                   "--on-unknown", "convert", "--on-regeneration", "convert",
                                   "--on-downgrade", "convert", "--no-preflight",
                                   "--delete-confirm-off"])
        check("recompressor: in place under the same long path",
              rc == 0 and md5(lr / f"{name}.jxl") != before, f"rc={rc}")
        lt = deep / "tr"
        _via_ascii(lambda s, o: ["magick", f"{A1}[0]", "-depth", "8", "-quality", "92",
                                 str(o)], None, lt / f"{name}.jpg").check_returncode()
        jm = md5(lt / f"{name}.jpg")
        rc, _ = run("h_long_tr", [TR, lt, "--mode", "1", "--force-convert", "--distance", "0"])
        ltj = lt / "converted_jxl" / f"{name}.jxl"
        ltr = deep / "trr"
        if ltj.exists():
            cp(ltj, ltr / f"{name}.jxl")
        rc2, _ = run("h_long_tr_rec", [TR, ltr, "--mode", "1", "--decode", "--force-transcode"])
        recs = list(ltr.rglob("*.jp*g"))
        check("transcoder: JPEG -> JXL -> JPEG bit-exact under the same long path",
              rc == 0 and rc2 == 0 and len(recs) == 1 and md5(recs[0]) == jm,
              f"rc={rc}/{rc2}")


def main():
    global B, LOGS
    # The scratch folder's name is Japanese; a redirected console is cp1252.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # EXPLORER_THUMBNAIL = auto covers lossy outputs when jxl-thumb is
    # installed: pinned to "not installed" so every check sees the same
    # outputs on every PC; the Explorer-thumbnail section sets 1 itself.
    os.environ["JXLPHOTO_JXL_THUMB"] = "0"
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixtures", type=Path,
                    default=(Path(os.environ["JXLPHOTO_FIXTURES"])
                             if os.environ.get("JXLPHOTO_FIXTURES") else None),
                    help="fixture folder (default: the JXLPHOTO_FIXTURES variable)")
    ap.add_argument("--work", type=Path, default=None,
                    help="scratch folder (default: <fixtures drive>\\jxlphoto_battery_<date>)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch folder")
    ap.add_argument("--ascii-paths", action="store_true",
                    help="run in a plain ASCII folder instead of the hostile one")
    args = ap.parse_args()
    if args.fixtures is None or not args.fixtures.is_dir():
        ap.error("pass --fixtures <folder> or set JXLPHOTO_FIXTURES "
                 "(see the module docstring for the layout)")

    for tool in ("cjxl", "djxl", "exiftool", "magick"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} is not on PATH")
    (A1, A2, A3), scan = pick_fixtures(args.fixtures)
    root = args.work or Path(args.fixtures.anchor) / f"jxlphoto_battery_{time.strftime('%y%m%d')}"
    B = root / ("work" if args.ascii_paths else HOSTILE_DIR)
    if root.exists():
        rmtree(root)
    LOGS = B / "logs"
    LOGS.mkdir(parents=True)
    logs_before = (sum(1 for p in (REPO / "Logs").rglob("*") if p.is_file())
                   if (REPO / "Logs").is_dir() else 0)
    t0 = time.time()
    try:
        section("Pool")
        pool = B / "pool"
        for s in (A1, A2, A3):
            cp(s, pool / s.name)
        rc, _ = run("pool_encode", [ENC, pool, "--mode", "1", "--distance", "1",
                                    "--effort", "3", "--no-preflight"])
        pool_jxl = pool / "converted_jxl"
        check("3 TIFFs encoded (mode 1)", rc == 0 and len(list(pool_jxl.glob("*.jxl"))) == 3)

        encoder_checks(A1, A2, A3, pool_jxl)
        decoder_checks(A1, pool_jxl)
        if scan is not None:
            multipage_and_recompressor_checks(A1, pool_jxl, scan)
        else:
            print("\n(no multi-page scan in raw_scan/: multi-page and in-place checks skipped)")
        recompressor_foreign_check(A1, A3, pool_jxl)
        overwrite_guard_checks(A1, A2)
        memory_cap_checks(A1, pool_jxl)
        derivative_rederive_checks(A1, pool_jxl)
        transcoder_checks(A1, A2)
        hostile_name_checks(A1)
        interaction_checks(A1, A2)
        explorer_thumbnail_checks(A1, pool_jxl)
        wrapper_checks(A1, A2)
        interrupt_checks(A1, A2, A3)
        if (args.fixtures / "TIFF16_varied").is_dir():
            varied_checks(args.fixtures / "TIFF16_varied")
        else:
            print("\n(no TIFF16_varied/: varied-export checks skipped)")
        if (args.fixtures / "TIFF_scans_varied").is_dir():
            scan_checks(args.fixtures / "TIFF_scans_varied")
        else:
            print("\n(no TIFF_scans_varied/: varied-scan checks skipped)")
        if (args.fixtures / "JPEG_varied").is_dir():
            jpeg_checks(args.fixtures / "JPEG_varied")
        else:
            print("\n(no JPEG_varied/: varied-JPEG checks skipped)")
        if (args.fixtures / "silverfast_xmp").is_dir():
            silverfast_checks(args.fixtures / "silverfast_xmp")
        else:
            print("\n(no silverfast_xmp/: SilverFast-original checks skipped)")
        if (args.fixtures / "TIFF_tagless").is_dir():
            tagless_checks(args.fixtures / "TIFF_tagless")
        else:
            print("\n(no TIFF_tagless/: tagless-camera-TIFF checks skipped)")
    finally:
        logs_after = (sum(1 for p in (REPO / "Logs").rglob("*") if p.is_file())
                      if (REPO / "Logs").is_dir() else 0)
        check("no script wrote to the repository's Logs\\",
              logs_before == logs_after, f"{logs_before} -> {logs_after}")
        n_ok = sum(1 for *_, ok, _ in results if ok)
        lines = [f"# Real-photo battery — {time.strftime('%Y-%m-%d %H:%M')}", "",
                 f"**{n_ok}/{len(results)} passed** in {time.time() - t0:.0f}s · "
                 f"fixtures `{args.fixtures}` · commit `"
                 + subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                                  capture_output=True, text=True).stdout.strip()
                 + "` (+ working tree)", ""]
        cur = None
        for sec, name, ok, d in results:
            if sec != cur:
                lines += ["", f"## {sec}", "", "| | Check | Detail |", "|---|---|---|"]
                cur = sec
            lines.append(f"| {'✅' if ok else '❌'} | {name} | {d.replace('|', '/')} |")
        report = REPO / "AI_tools" / f"{time.strftime('%y%m%d')}_battery_report.md"
        report.parent.mkdir(exist_ok=True)
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n{n_ok}/{len(results)} passed — report: {report}")
        if not args.keep and n_ok == len(results):
            rmtree(root)
        elif not args.keep:
            print(f"Scratch folder KEPT for inspection (a check failed): {B}")
    sys.exit(0 if results and all(ok for *_, ok, _ in results) else 1)


if __name__ == "__main__":
    main()
