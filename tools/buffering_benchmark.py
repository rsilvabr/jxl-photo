#!/usr/bin/env python3
"""Whole-image (cjxl's default) vs streaming (--buffering 1) encodes, measured.

For each image: converts it once to a 16-bit sRGB PNG reference (first page
only), then encodes it with cjxl at effort 7 (d = 2.5 / 3 / 3.5) and effort 9
(d = 3), each with cjxl's default buffering and with --buffering 1, and
records file size, wall and CPU time, peak memory, SSIMULACRA2 and Butteraugli
(max and 3-norm, at d = 3) against the reference.

The results behind "Streaming vs whole-image" in
docs/README_jxl_recompressor.md. Re-run it after a libjxl upgrade: cjxl's
choice between the two paths, and their cost, can change between versions.

    py tools/buffering_benchmark.py E:\\photos\\a.tif E:\\photos\\b.tif --work D:\\scratch

Needs cjxl, djxl, ssimulacra2 and butteraugli_main (libjxl tools), ImageMagick
and psutil. The source images are only read; everything is written under
--work. Results go to <work>/buffering_benchmark.csv (resumable: rows already
there are skipped).
"""
import argparse
import csv
import re
import subprocess
import time
from pathlib import Path

import psutil

DEFAULT_SRGB = Path(r"C:\Windows\System32\spool\drivers\color\sRGB Color Space Profile.icm")
# (effort, distance, buffering or None, run butteraugli)
CASES = ([(7, d, b, d == 3.0) for d in (2.5, 3.0, 3.5) for b in (None, 1)]
         + [(9, 3.0, b, True) for b in (None, 1)])


def run_measured(cmd):
    """(wall s, CPU s, peak RSS GB) of one process. CPU time is what limits a
    batch with many workers; wall time is what one file takes."""
    t = time.perf_counter()
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    ps = psutil.Process(p.pid)
    peak, cpu = 0, None
    while p.poll() is None:
        try:
            peak = max(peak, ps.memory_info().rss)
            cpu = ps.cpu_times()
        except psutil.Error:
            pass
        time.sleep(0.05)
    wall = time.perf_counter() - t
    try:
        cpu = ps.cpu_times()   # the handle is still open: final totals
    except psutil.Error:
        pass
    err = p.stderr.read().decode(errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed: {err[-400:]}")
    return wall, (cpu.user + cpu.system) if cpu else 0.0, peak / 2**30


def floats(text):
    return [float(x) for x in re.findall(r"-?\d+\.\d+(?:e-?\d+)?", text)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("images", nargs="+", type=Path,
                    help="source images (16-bit TIFF/PNG; first page used)")
    ap.add_argument("--work", type=Path, required=True,
                    help="scratch folder (references, encodes, the CSV)")
    ap.add_argument("--srgb-icc", type=Path, default=DEFAULT_SRGB,
                    help="sRGB profile for the reference (default: Windows')")
    args = ap.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    out_csv = args.work / "buffering_benchmark.csv"
    version = subprocess.run(["cjxl", "--version"], capture_output=True,
                             text=True).stdout.splitlines()[0]
    print(version, flush=True)

    rows, done = [], set()
    if out_csv.exists():
        with open(out_csv, newline="") as fh:
            rows = list(csv.DictReader(fh))
        done = {(r["image"], r["effort"], r["distance"], r["mode"]) for r in rows}

    for img in args.images:
        ref = args.work / (img.stem + "_srgb16.png")
        if not ref.exists():
            subprocess.run(["magick", f"{img}[0]", "-profile", str(args.srgb_icc),
                            "-depth", "16", str(ref)], check=True,
                           capture_output=True)
        w, h = map(int, subprocess.run(
            ["magick", "identify", "-format", "%w %h", str(ref)],
            capture_output=True, text=True, check=True).stdout.split())
        for effort, dist, buf, do_ba in CASES:
            mode = "streaming" if buf else "default"
            if (img.stem, str(effort), str(dist), mode) in done:
                continue
            jxl = args.work / f"{img.stem}_e{effort}_d{dist}_{mode}.jxl"
            dec = args.work / f"{img.stem}_e{effort}_d{dist}_{mode}_dec.png"
            cmd = ["cjxl", str(ref), str(jxl), "-d", str(dist), "-e", str(effort)]
            if buf is not None:
                cmd.append(f"--buffering={buf}")
            wall, cpu, peak = run_measured(cmd)
            subprocess.run(["djxl", str(jxl), str(dec), "--bits_per_sample=16"],
                           check=True, capture_output=True)
            ssim = floats(subprocess.run(["ssimulacra2", str(ref), str(dec)],
                                         capture_output=True, text=True,
                                         check=True).stdout)[-1]
            ba_max = ba_p3 = ""
            if do_ba:
                f = floats(subprocess.run(
                    ["butteraugli_main", str(ref), str(dec), "--pnorm", "3"],
                    capture_output=True, text=True, check=True).stdout)
                ba_max, ba_p3 = round(f[0], 3), round(f[-1], 3)
            row = {"cjxl": version, "image": img.stem, "megapixels": round(w * h / 1e6, 1),
                   "effort": effort, "distance": dist, "mode": mode,
                   "bytes": jxl.stat().st_size,
                   "bpp": round(jxl.stat().st_size * 8 / (w * h), 4),
                   "wall_s": round(wall, 1), "cpu_s": round(cpu, 1),
                   "peak_gb": round(peak, 2), "ssimulacra2": round(ssim, 3),
                   "ba_max": ba_max, "ba_p3norm": ba_p3}
            rows.append(row)
            print(row, flush=True)
            with open(out_csv, "w", newline="") as fh:
                wr = csv.DictWriter(fh, fieldnames=list(row))
                wr.writeheader()
                wr.writerows(rows)
            dec.unlink()
    print(f"Done: {out_csv}")


if __name__ == "__main__":
    main()
