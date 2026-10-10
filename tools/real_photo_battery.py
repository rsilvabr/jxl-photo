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

The scratch folder (default <fixtures drive>\\jxlphoto_battery_<date>) is
deleted at the end unless --keep. The report is written to
AI_tools/<YYMMDD>_battery_report.md (gitignored) and printed. Exit code 0 when
every check passed, 1 otherwise. Needs cjxl, djxl, exiftool and magick on PATH.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
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
    r = subprocess.run(["exiftool"] + [str(a) for a in args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip()


def relation(path):
    """XMP-dc:Relation values as a list. exiftool -j keeps list items separate,
    while -s3 would join them with ', ' on one line."""
    r = subprocess.run(["exiftool", "-j", "-XMP-dc:Relation", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        rel = json.loads(r.stdout)[0].get("Relation")
    except Exception:
        return []
    return [] if rel is None else [str(v) for v in (rel if isinstance(rel, list) else [rel])]


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


def cp(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def raw_rgb16(png, shape):
    """djxl's own PNG as raw little-endian 16-bit RGB (no colour management)."""
    r = subprocess.run(["magick", str(png), "-depth", "16", "-endian", "LSB", "rgb:-"],
                       capture_output=True)
    a = np.frombuffer(r.stdout, dtype="<u2")
    return a.reshape(shape) if a.size == int(np.prod(shape)) else None


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
        subprocess.run(["djxl", str(jxl), str(ref_png), "--bits_per_sample=16"],
                       capture_output=True)
        ref = raw_rgb16(ref_png, dec.shape)
        mse = float(np.mean((master - dec.astype(np.float64)) ** 2))
        psnr = 10 * np.log10(65535.0 ** 2 / mse) if mse else 999.0
        check(f"d={dist}: original ICC restored byte-for-byte", icc_same)
        check(f"d={dist}: pixels == an independent djxl decode",
              ref is not None and np.array_equal(ref, dec), f"PSNR vs master {psnr:.1f} dB")

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

    ARGS = ["--mode", "8", "--distance", "2", "--effort", "3", "--on-unknown", "convert",
            "--on-regeneration", "convert", "--on-downgrade", "convert", "--no-preflight",
            "--workers", "8", "--delete-confirm-off"]
    r = B / "r_inplace"
    for p in pages:
        cp(p, r / p.name)
    cp(pool_jxl / A1.with_suffix(".jxl").name, r / "single.jxl")
    before = {p.name: md5(p) for p in r.glob("*.jxl")}
    rc, _ = run("rec_inplace", [REC, r] + ARGS)
    changed = sorted(n for n in before if md5(r / n) != before[n])
    check("in place: a single file AND every page of a group replaced, no .tmp left",
          rc == 0 and len(changed) == len(before) and not list(r.glob("*.tmp")),
          f"rc={rc} changed={changed}")
    check("in place: jxlphoto-mpg markers kept on the replaced pages",
          all("jxlphoto-mpg" in exif(["-s3", "-XMP-dc:Relation", r / p.name]) for p in pages))
    lin = exif(["-s3", "-XMP-dc:Description", r / "single.jxl"])
    check("in place: lineage records the second generation", "gen=2" in lin, lin[:120])

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
          and subprocess.run(["djxl", str(outs[0]), str(B / "mem_rec.png")],
                             capture_output=True).returncode == 0, f"rc={rc}")
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
    master_origins = origins_of(r / "p.jxl")
    origins = origins_of(outs[0]) if outs else []
    check("recompressor: the derivative keeps the master's origin",
          origins == master_origins == ["jxlphoto-origin:tiff16"],
          f"master={master_origins} derivative={origins}")
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
    subprocess.run(["magick", f"{A1}[0]", "-depth", "8", "-quality", "92", str(t / "j.jpg")],
                   check=True, capture_output=True)
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
    subprocess.run(["magick", f"{A2}[0]", "-resize", "25%", "-depth", "16", str(a / "b.png")],
                   check=True, capture_output=True)
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


def main():
    global B, LOGS
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixtures", type=Path,
                    default=(Path(os.environ["JXLPHOTO_FIXTURES"])
                             if os.environ.get("JXLPHOTO_FIXTURES") else None),
                    help="fixture folder (default: the JXLPHOTO_FIXTURES variable)")
    ap.add_argument("--work", type=Path, default=None,
                    help="scratch folder (default: <fixtures drive>\\jxlphoto_battery_<date>)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch folder")
    args = ap.parse_args()
    if args.fixtures is None or not args.fixtures.is_dir():
        ap.error("pass --fixtures <folder> or set JXLPHOTO_FIXTURES "
                 "(see the module docstring for the layout)")

    for tool in ("cjxl", "djxl", "exiftool", "magick"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} is not on PATH")
    (A1, A2, A3), scan = pick_fixtures(args.fixtures)
    B = args.work or Path(args.fixtures.anchor) / f"jxlphoto_battery_{time.strftime('%y%m%d')}"
    if B.exists():
        shutil.rmtree(B)
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
        memory_cap_checks(A1, pool_jxl)
        derivative_rederive_checks(A1, pool_jxl)
        transcoder_checks(A1, A2)
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
            shutil.rmtree(B, ignore_errors=True)
        elif not args.keep:
            print(f"Scratch folder KEPT for inspection (a check failed): {B}")
    sys.exit(0 if results and all(ok for *_, ok, _ in results) else 1)


if __name__ == "__main__":
    main()
