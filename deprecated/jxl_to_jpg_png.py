#!/usr/bin/env python3
"""
jxl_to_jpeg.py — Batch JXL → JPEG (or PNG) converter with color profile conversion

Finds all JXLs recursively under the input folder and converts them to JPEG or PNG,
applying color space conversion (e.g. ProPhoto RGB → sRGB) via ICC profiles.

Usage:
  py jxl_to_jpeg.py <input> [--mode 0-2] [--quality 95] [--format jpeg|png]
                    [--icc-profile path/to/profile.icc] [--workers 8]
                    [--output-name NAME] [--bit-depth 8|16]
                    [--staging E:\\staging] [--ram] [--overwrite] [--dry-run] [--no-log]

Requirements:
  djxl        https://github.com/libjxl/libjxl/releases  (same package as cjxl)
  ImageMagick https://imagemagick.org  (magick command)
"""

import subprocess, os, sys, shutil, logging, tempfile, threading, argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ─────────────────────────────────────────────
# USER SETTINGS (can be overridden by CLI args)
# ─────────────────────────────────────────────

OUTPUT_FORMAT   = "jpeg"
# Output format: "jpeg" or "png"

JPEG_QUALITY    = 95
# JPEG quality (1-100). 95 is high quality, good for printing and archiving.
# Ignored when OUTPUT_FORMAT = "png"

BIT_DEPTH       = 8
# Output bit depth: 8 (standard JPEG/PNG) or 16 (PNG only, for printing workflows)
# JPEG does not support 16-bit — if you set 16 with jpeg, script switches to png automatically.

OUTPUT_ICC      = None
# Path to the output ICC profile for color conversion.
# None → uses ImageMagick's built-in sRGB profile (standard web/print sRGB)
# Example: r"C:\tools\icc\AdobeRGB1998.icc"
# The JXL's embedded ICC (ProPhoto, AdobeRGB, etc.) is used as the source profile.

OUTPUT_FOLDER_MODE = 1
# 0 → subfolder inside the JXL folder:         .../JXLs/jpeg-srgb/photo.jpg
# 1 → sibling folder next to the JXL folder:   .../session/jpeg-srgb/photo.jpg  [default]
# 2 → same folder name + suffix:               .../16B_JXL_lossy_srgb/photo.jpg

OUTPUT_FOLDER_NAME = "jpeg-srgb"
# Name of the output folder (modes 0 and 1).

OUTPUT_FOLDER_SUFFIX = "_srgb"
# Suffix appended to the JXL folder name (mode 2 only).
# Example: 16B_JXL_lossy → 16B_JXL_lossy_srgb

USE_RAM         = True
# True  → PNG intermediate from djxl stays in RAM, piped directly to magick
#          Faster, no temp file on disk (~200MB RAM per worker)
# False → PNG is written to disk in TEMP_DIR before magick reads it

TEMP_DIR        = None
# Temp directory for intermediate PNG files when USE_RAM=False.
# None → system temp (usually C:\Users\...\AppData\Local\Temp)

STAGING_DIR     = None
# Staging directory for output files during conversion.
# None → write directly to final destination
# If set: files written here first, moved in bulk when each folder group is done.
# Useful to separate read I/O (SSD with JXLs) from write I/O (HDD for JPEGs).
# Example: r"E:\staging"

OVERWRITE       = False
# False → skip if output file already exists
# True  → always overwrite

FILENAME_REPLACE_FROM = "ProPhotoRGB"
FILENAME_REPLACE_TO   = "sRGB"
# Replace a string in the output filename.
# Example: FROM="ProPhotoRGB" TO="sRGB" renames
#   _DSC4550_ProPhotoRGB_v1.jxl → _DSC4550_sRGB_v1.jpg
# Leave both empty to keep original filename.
# Case-sensitive. Only replaces the first occurrence.

ENABLE_LOG      = True
# True  → write a timestamped log file
# False → output to terminal only

# ─────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent
LOG_DIR      = SCRIPT_DIR / "Logs" / Path(__file__).stem
logger       = None
counter_lock = threading.Lock()
_counter     = {"done": 0, "total": 0}


def setup_logger(enable: bool):
    global logger
    logger = logging.getLogger("jxl_to_jpeg")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    log_file = None
    if enable:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = LOG_DIR / f"{ts}.log"
        fh       = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.info(f"Log: {log_file}")

    return log_file


def next_count():
    with counter_lock:
        _counter["done"] += 1
        return _counter["done"], _counter["total"]


def resolve_output_path(jxl_path: Path, mode: int, output_name: str, suffix: str,
                        ext: str, rename_from: str = "", rename_to: str = "") -> Path:
    """Computes the output file path for a given JXL based on the selected mode."""
    stem = jxl_path.stem
    # Apply filename string replacement if configured
    if rename_from and rename_from in stem:
        stem = stem.replace(rename_from, rename_to, 1)

    if mode == 0:
        # Subfolder inside the JXL folder
        return jxl_path.parent / output_name / f"{stem}.{ext}"

    elif mode == 1:
        # Sibling folder next to the JXL folder
        return jxl_path.parent.parent / output_name / f"{stem}.{ext}"

    elif mode == 2:
        # Same name as JXL folder + suffix
        new_folder = jxl_path.parent.name + suffix
        return jxl_path.parent.parent / new_folder / f"{stem}.{ext}"

    raise ValueError(f"Invalid mode: {mode}")


def convert_one(jxl_path: Path, write_path: Path, final_path: Path,
                quality: int, fmt: str, bit_depth: int,
                output_icc: str, use_ram: bool) -> tuple:
    """
    Converts a single JXL to JPEG or PNG.

    Pipeline:
      1. djxl decodes JXL → PNG (preserves embedded ICC, full bit depth)
      2. magick applies ICC conversion (source ICC from PNG → target ICC)
      3. magick outputs JPEG or PNG at desired quality/bit depth

    Uses RAM pipeline (djxl stdout → magick stdin) if use_ram=True.
    """
    overwritten = final_path.exists()
    if overwritten and not OVERWRITE:
        n, total = next_count()
        logger.info(f"[{n}/{total}] SKIP (exists) | {jxl_path.name}")
        return (str(jxl_path), "skipped", str(final_path))

    write_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Build magick output args
        magick_output = []

        # Apply ICC color conversion
        # -profile without prior -profile = assign source (already embedded in PNG from djxl)
        # Provide target ICC
        if output_icc:
            magick_output += ["-profile", output_icc]
        else:
            # Use ImageMagick's built-in sRGB
            magick_output += ["-colorspace", "sRGB"]
            # Note: -set exif:ColorSpace doesn't reliably write to JPEG EXIF.
            # ColorSpace tag is fixed via exiftool after conversion instead.

        # Bit depth
        magick_output += ["-depth", str(bit_depth)]

        # Format-specific settings
        if fmt == "jpeg":
            if bit_depth == 16:
                # JPEG doesn't support 16-bit — warn and use PNG instead
                logger.warning(f"  JPEG doesn't support 16-bit, switching to PNG | {jxl_path.name}")
                fmt = "png"
                actual_out = write_path.with_suffix(".png")
            else:
                magick_output += ["-quality", str(quality)]
                actual_out = write_path
        else:
            actual_out = write_path

        if use_ram:
            # RAM pipeline: djxl → stdout → magick stdin
            djxl_proc = subprocess.Popen(
                ["djxl", str(jxl_path), "-", "--output_format=png"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            magick_cmd = ["magick", "-"] + magick_output + [str(actual_out)]
            magick_proc = subprocess.Popen(
                magick_cmd,
                stdin=djxl_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            djxl_proc.stdout.close()
            _, magick_err = magick_proc.communicate()
            djxl_proc.wait()

            if djxl_proc.returncode != 0:
                err = djxl_proc.stderr.read().decode(errors="replace")[:200]
                raise RuntimeError(f"djxl failed: {err}")
            if magick_proc.returncode != 0:
                raise RuntimeError(f"magick failed: {magick_err.decode(errors='replace')[:200]}")

        else:
            # Disk pipeline: djxl → temp PNG → magick → output
            with tempfile.TemporaryDirectory(prefix="jxl2jpg_", dir=TEMP_DIR) as tmp:
                tmp_png = Path(tmp) / f"{jxl_path.stem}.png"
                r1 = subprocess.run(
                    ["djxl", str(jxl_path), str(tmp_png)],
                    capture_output=True
                )
                if r1.returncode != 0:
                    raise RuntimeError(f"djxl failed: {r1.stderr.decode(errors='replace')[:200]}")

                magick_cmd = ["magick", str(tmp_png)] + magick_output + [str(actual_out)]
                r2 = subprocess.run(magick_cmd, capture_output=True)
                if r2.returncode != 0:
                    raise RuntimeError(f"magick failed: {r2.stderr.decode(errors='replace')[:200]}")

        n, total = next_count()
        label = "OVERWRITE" if overwritten else "OK"

        # Fix ColorSpace EXIF tag — magick doesn't reliably write it for sRGB
        # ColorSpace=1 means sRGB (standard EXIF value)
        if fmt == "jpeg" and not output_icc:
            cs_arg = Path(tempfile.mktemp(suffix=".args"))
            cs_arg.write_text(f"-overwrite_original\n-exif:ColorSpace=\n{actual_out}\n",
                              encoding="utf-8")
            subprocess.run(["exiftool", "-@", str(cs_arg)], capture_output=True)
            cs_arg.unlink(missing_ok=True)

        logger.info(f"[{n}/{total}] {label} | {jxl_path.name} → {actual_out.name}")
        return (str(jxl_path), "overwrite" if overwritten else "ok", str(actual_out))

    except Exception as e:
        n, total = next_count()
        logger.error(f"[{n}/{total}] ERROR | {jxl_path.name} | {e}")
        return (str(jxl_path), "error", str(e))


def process_group(group_pairs: list, workers: int, quality: int, fmt: str,
                  bit_depth: int, output_icc: str, use_ram: bool) -> list:
    """
    Converts a group of (jxl, final_output) pairs in parallel.
    If STAGING_DIR is set, writes to staging first then moves in bulk.
    """
    use_staging = STAGING_DIR is not None
    staging_dir = Path(STAGING_DIR) if use_staging else None
    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    ext = fmt if fmt != "jpeg" else "jpg"
    tasks = []
    for jxl, final_out in group_pairs:
        if use_staging:
            write_out = staging_dir / f"{jxl.parent.name}__{jxl.stem}.{ext}"
        else:
            write_out = final_out
        tasks.append((jxl, write_out, final_out))

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(convert_one, j, w, f, quality, fmt, bit_depth, output_icc, use_ram): (j, w, f)
            for j, w, f in tasks
        }
        for fut in as_completed(futures):
            results.append(fut.result())

    if use_staging:
        moved = 0
        for jxl, write_out, final_out in tasks:
            if write_out.exists():
                final_out.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_out), str(final_out))
                moved += 1
        if moved:
            logger.info(f"  → Moved {moved} file(s) from staging to destination")

    return results


def find_jxls(input_path: Path) -> list:
    """Recursively finds all JXL files, deduplicating via resolve()."""
    seen  = set()
    files = []
    for f in input_path.rglob("*.jxl"):
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            files.append(f)
    return sorted(files)


def main():
    global OVERWRITE, STAGING_DIR

    parser = argparse.ArgumentParser(
        description="Batch JXL → JPEG/PNG converter with ICC color profile conversion"
    )
    parser.add_argument("input", type=Path,
                        help="Input root folder (searched recursively for JXLs)")
    parser.add_argument("--mode", type=int, default=OUTPUT_FOLDER_MODE,
                        choices=[0, 1, 2],
                        help="Output folder mode: 0=subfolder inside JXL folder, "
                             "1=sibling folder [default], 2=JXL folder name + suffix")
    parser.add_argument("--output-name", type=str, default=OUTPUT_FOLDER_NAME,
                        help=f"Output folder name for modes 0 and 1 (default: {OUTPUT_FOLDER_NAME})")
    parser.add_argument("--output-suffix", type=str, default=OUTPUT_FOLDER_SUFFIX,
                        help=f"Suffix appended to folder name in mode 2 (default: {OUTPUT_FOLDER_SUFFIX})")
    parser.add_argument("--format", type=str, default=OUTPUT_FORMAT,
                        choices=["jpeg", "png"],
                        help=f"Output format (default: {OUTPUT_FORMAT})")
    parser.add_argument("--quality", type=int, default=JPEG_QUALITY,
                        help=f"JPEG quality 1-100 (default: {JPEG_QUALITY}, ignored for PNG)")
    parser.add_argument("--bit-depth", type=int, default=BIT_DEPTH,
                        choices=[8, 16],
                        help=f"Output bit depth (default: {BIT_DEPTH}; 16-bit forces PNG)")
    parser.add_argument("--icc-profile", type=str, default=OUTPUT_ICC,
                        help="Path to output ICC profile for color conversion "
                             "(default: built-in sRGB). Example: C:\\icc\\AdobeRGB1998.icc")
    parser.add_argument("--workers", type=int, default=min(os.cpu_count(), 8),
                        help="Parallel worker threads (default: CPU count, max 8)")
    parser.add_argument("--staging", type=str, default=STAGING_DIR,
                        help="Staging directory for output files (default: disabled)")
    parser.add_argument("--ram", action="store_true", default=USE_RAM,
                        help="Keep PNG intermediate in RAM (djxl pipe → magick stdin)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing output files")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be converted without converting")
    parser.add_argument("--no-log", action="store_true",
                        help="Disable log file (terminal output only)")
    parser.add_argument("--rename-from", type=str, default=FILENAME_REPLACE_FROM,
                        help="String to replace in output filename (e.g. 'ProPhotoRGB')")
    parser.add_argument("--rename-to", type=str, default=FILENAME_REPLACE_TO,
                        help="Replacement string in output filename (e.g. 'sRGB')")
    args = parser.parse_args()

    # Apply CLI overrides to globals
    if args.overwrite: OVERWRITE = True
    if args.staging:   STAGING_DIR = args.staging

    log_file = setup_logger(not args.no_log)

    ext = args.format if args.format != "jpeg" else "jpg"
    icc_label = Path(args.icc_profile).stem if args.icc_profile else "sRGB (built-in)"

    logger.info(
        f"Mode: {args.mode} | Format: {args.format} | Quality: {args.quality} | "
        f"Bit depth: {args.bit_depth} | ICC: {icc_label} | "
        f"RAM: {args.ram} | Staging: {STAGING_DIR or 'disabled'} | "
        f"Overwrite: {OVERWRITE} | Workers: {args.workers} | DryRun: {args.dry_run}"
    )
    if args.rename_from:
        logger.info(f"Filename rename: '{args.rename_from}' → '{args.rename_to}'")
    logger.info(f"Input: {args.input}")

    jxls = find_jxls(args.input)
    if not jxls:
        logger.warning("No JXL files found.")
        return

    _counter["total"] = len(jxls)
    logger.info(f"JXLs found: {len(jxls)}")

    # Build (jxl, output) pairs
    pairs = []
    for j in jxls:
        out = resolve_output_path(j, args.mode, args.output_name,
                                  args.output_suffix, ext,
                                  args.rename_from, args.rename_to)
        pairs.append((j, out))

    if args.dry_run:
        for j, out in pairs:
            logger.info(f"  DRY | {j.name} → {out}")
        logger.info(f"Dry run: {len(pairs)} files would be converted.")
        return

    # Group by output folder for staging efficiency
    groups: dict[Path, list] = {}
    for j, out in pairs:
        groups.setdefault(out.parent, []).append((j, out))

    logger.info(f"Output groups: {len(groups)}")

    ok = err = skipped = overwritten = 0

    for dest_folder, group_pairs in groups.items():
        if len(groups) > 1:
            logger.info(f"── Group: {dest_folder} ({len(group_pairs)} file(s))")

        results = process_group(
            group_pairs, args.workers,
            args.quality, args.format, args.bit_depth,
            args.icc_profile, args.ram
        )

        for _, status, _ in results:
            if   status == "ok":        ok += 1
            elif status == "overwrite": ok += 1; overwritten += 1
            elif status == "skipped":   skipped += 1
            elif status == "error":     err += 1

    logger.info(f"\n{'─'*50}")
    logger.info(f"Done: {ok} OK | {overwritten} overwrites | {skipped} skipped | {err} errors")
    if log_file:
        logger.info(f"Log: {log_file}")


if __name__ == "__main__":
    main()
