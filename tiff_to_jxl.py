#!/usr/bin/env python3
"""
tiff_to_jxl.py — Batch TIFF 16-bit → JPEG XL converter

Usage:
  py tiff_to_jxl.py <input> [output] --mode 0-5 [--workers N] [--overwrite] [--sync]

Requirements:
  pip install tifffile numpy
  cjxl / djxl  →  https://github.com/libjxl/libjxl/releases
  exiftool     →  https://exiftool.org
"""

import subprocess, os, tempfile, threading, zlib, struct, logging, sys, shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import argparse
import numpy as np
import tifffile

# ─────────────────────────────────────────────
# USER SETTINGS - GENERAL
# ─────────────────────────────────────────────

CJXL_EFFORT = 7
# Compression effort (1–10).
# Controls output file size — does NOT affect quality (quality is set by CJXL_DISTANCE).
# Higher effort = smaller file, but more CPU time.
# 7 is the sweet spot for camera photos — effort 8-10 is much slower and can
# increase file size for high-ISO or texture-heavy images.

CJXL_DISTANCE = 1
# 0   = mathematically lossless (pixel-perfect)
# 0.1 = near-lossless (~25MB for 36MP), imperceptible difference
# 0.5 = high quality lossy — recommended starting point (libjxl authors)
# 1.0 = "visually lossless" per libjxl documentation
# ⚠️  Do not use lossy for images you still intend to edit.

USE_RAM_FOR_PNG = False
# True  → PNG intermediate stays entirely in RAM (faster, ~400MB RAM per worker)
# False → PNG is written to disk in TEMP_DIR (useful if RAM is limited)

TEMP_DIR = None
# Pasta para arquivos temporários (EXIF bin e PNG se USE_RAM_FOR_PNG=False).
# None → usa pasta temp do sistema (normalmente C:\Users\...\AppData\Local\Temp)
# Ex:  → "E:\\temp_jxl"

TEMP2_DIR = None
# Staging directory for output JXLs during conversion.
# None → disabled: JXLs are written directly to their final destination.
# If set: JXLs are written here during conversion, then moved in bulk to the final
# destination when each folder group finishes. Separates read I/O (HDD with TIFFs)
# from write I/O (SSD for new JXLs), reducing seek contention on HDDs.
# Example: r"E:\\staging_jxl"

OVERWRITE = True
# False   → skip JXLs that already exist at the final destination. Safe for resuming.
# True    → always overwrite existing JXLs.
# "smart" → same as --sync flag: only reconvert if the TIFF is newer than the JXL.
#            Useful after re-editing and re-exporting from Capture One.
# Never overwrites TIFFs or any other non-JXL format.

ENCODE_TAG_MODE = "xmp"
# Records encoding parameters (distance and effort) in the JXL metadata.
# "software" → appends to the EXIF Software field (e.g. "Capture One | cjxl d=0.5 e=7")
#              Visible in IrfanView, exiftool, and most viewers.
# "xmp"      → writes as XMP-dc:Description custom field
#              Cleaner — does not touch the original Software field
#              Visible in Windows Properties, but not in IrfanView
# "off"      → does not add anything



# ─────────────────────────────────────────────
# USER SETTINGS - MODES CONFIGURATION
# ─────────────────────────────────────────────


# || MODE 0 SETTINGS ||
# No settings needed. Just use 
# py convert_jxl.py <input> <output> [--mode 0] [--workers N] [--overwrite] [--sync]

# || MODE 1 SETTINGS ||
CONVERTED_JXL_FOLDER = "converted_jxl"
# [MODE 1] Name of the subfolder created inside each TIFF folder.
# Example: .../TIFF_FOLDER/converted_jxl/photo.jxl

# || MODE 2 SETTINGS ||
JXL_FOLDER_NAME = "JXL_16bits"
# [MODE 2] Name of the output folder created in the parent of each TIFF folder.
# Example: .../JXL_FOLDER_NAME/photo.jxl

# || MODE 3 SETTINGS ||
TIFF_SUFFIX_TO_REPLACE = "TIFF"
JXL_SUFFIX_REPLACE     = "JXL"
# [MODE 3] Replaces TIFF_SUFFIX_TO_REPLACE with JXL_SUFFIX_REPLACE in the folder name.
# Case-insensitive (TIFF, tiff, Tiff all match).
# Example: C1_Export_1_TIFF → C1_Export_1_JXL

# || MODES 4 and 5 SETTINGS ||
EXPORT_MARKER     = "_EXPORT"
EXPORT_JXL_FOLDER = "16B_JXL"
# [MODE 4/5] Uses EXPORT_MARKER as an anchor in the path.
# All JXLs go into EXPORT_MARKER/EXPORT_JXL_FOLDER/.
# Mode 4: processes TIFFs both inside and outside EXPORT_MARKER (same parent hierarchy).
# Mode 5: only processes TIFFs inside EXPORT_MARKER (ignores TIFFs outside).
#
# TIFFs inside EXPORT_MARKER: immediate subfolder (e.g. color space name) is dropped.
# TIFFs outside EXPORT_MARKER (mode 4 only): relative path from EXPORT_MARKER's parent is preserved.
#
# Example (mode 5, EXPORT_TIFF_SUBFOLDER = "TIFF16"):
#   EXPORT_MARKER/TIFF16/photo.tif      →  EXPORT_MARKER/EXPORT_JXL_FOLDER/photo.jxl
#   EXPORT_MARKER/AdobeRGB/photo.tif    →  ignored
#   EXPORT_MARKER/sRGB/photo.tif        →  ignored

EXPORT_TIFF_SUBFOLDER = ""
# [MODE 5] If set, only TIFFs in this specific subfolder of EXPORT_MARKER are processed,
# and this subfolder name is dropped from the output path.
# If empty (""), all TIFFs inside EXPORT_MARKER are processed (first subfolder is dropped).
# OBS: Empty value can cause filename collisions if different subfolders contain files
# with the same name (e.g. AdobeRGB/photo.tif and TIFF16/photo.tif).


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────


SCRIPT_DIR = Path(__file__).parent
LOG_DIR    = SCRIPT_DIR / "Logs" / Path(__file__).stem
logger     = None
counter_lock = threading.Lock()
_counter = {"done": 0, "total": 0}

def setup_logger():
    global logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = LOG_DIR / f"{timestamp}.log"

    logger = logging.getLogger("jxl_convert")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info(f"Log saved to: {log_file}")
    return log_file

def next_count():
    with counter_lock:
        _counter["done"] += 1
        return _counter["done"], _counter["total"]

def resolve_output(tiff_path: Path, mode: int, input_root: Path) -> Path:
    if mode == 0:
        return input_root / tiff_path.with_suffix(".jxl").name

    elif mode == 1:
        return tiff_path.parent / CONVERTED_JXL_FOLDER / tiff_path.with_suffix(".jxl").name

    elif mode == 2:
        return tiff_path.parent.parent / JXL_FOLDER_NAME / tiff_path.with_suffix(".jxl").name

    elif mode == 3:
        old_name = tiff_path.parent.name
        new_name = None
        for variant in [TIFF_SUFFIX_TO_REPLACE, TIFF_SUFFIX_TO_REPLACE.lower(),
                        TIFF_SUFFIX_TO_REPLACE.title()]:
            if variant in old_name:
                new_name = old_name.replace(variant, JXL_SUFFIX_REPLACE)
                break
        if new_name is None:
            # Suffix not found in folder name — append JXL suffix instead
            new_name = old_name + "_" + JXL_SUFFIX_REPLACE
            logger.warning(f"'{TIFF_SUFFIX_TO_REPLACE}' not found in '{old_name}', using '{new_name}'")
        return tiff_path.parent.parent / new_name / tiff_path.with_suffix(".jxl").name

    elif mode == 4:
        # Localiza _EXPORT no caminho
        parts = tiff_path.parts
        export_idx = next((i for i, p in enumerate(parts) if EXPORT_MARKER in p), None)
        if export_idx is None:
            logger.warning(f"'{EXPORT_MARKER}' not found in {tiff_path}, using local folder")
            return tiff_path.parent / EXPORT_JXL_FOLDER / tiff_path.with_suffix(".jxl").name

        export_dir    = Path(*parts[:export_idx + 1])   # .../03_D810/_EXPORT
        project_root  = export_dir.parent               # .../03_D810

        if tiff_path.is_relative_to(export_dir):
            # TIFF está dentro do _EXPORT — dropa a subpasta imediata (ex: AdobeRGB/)
            # preserva hierarquia mais profunda
            rel_parts = tiff_path.relative_to(export_dir).parts  # (AdobeRGB, sub, foto.tif)
            if len(rel_parts) > 1:
                rel = Path(*rel_parts[1:])   # dropa AdobeRGB → sub/foto.tif
            else:
                rel = Path(rel_parts[0])     # direto na raiz do _EXPORT (sem subpasta)
        else:
            # TIFF está fora do _EXPORT mas na mesma hierarquia — preserva caminho
            # relativo à pasta pai do _EXPORT (project_root)
            # ex: C1_TIFF_16BIT/bbb.tiff → 16B_JXL/C1_TIFF_16BIT/bbb.jxl
            rel = tiff_path.relative_to(project_root)

        return export_dir / EXPORT_JXL_FOLDER / rel.with_suffix(".jxl")

    elif mode == 5:
        # Igual ao modo 4 para TIFFs dentro do _EXPORT, mas:
        # se EXPORT_TIFF_SUBFOLDER definido, dropa especificamente essa subpasta
        parts = tiff_path.parts
        export_idx = next((i for i, p in enumerate(parts) if EXPORT_MARKER in p), None)
        if export_idx is None:
            logger.warning(f"'{EXPORT_MARKER}' not found in {tiff_path}, using local folder")
            return tiff_path.parent / EXPORT_JXL_FOLDER / tiff_path.with_suffix(".jxl").name

        export_dir = Path(*parts[:export_idx + 1])

        if EXPORT_TIFF_SUBFOLDER:
            # Dropa a subpasta específica configurada, preserva o resto
            # ex: _EXPORT/AdobeRGB/sub/foto.tif → _EXPORT/16B_JXL/sub/foto.jxl
            anchor = export_dir / EXPORT_TIFF_SUBFOLDER
            rel = tiff_path.relative_to(anchor)
        else:
            # Dropa a primeira subpasta após _EXPORT (qualquer que seja)
            # ex: _EXPORT/AdobeRGB/foto.tif → _EXPORT/16B_JXL/foto.jxl
            rel_parts = tiff_path.relative_to(export_dir).parts
            rel = Path(*rel_parts[1:]) if len(rel_parts) > 1 else Path(rel_parts[0])

        return export_dir / EXPORT_JXL_FOLDER / rel.with_suffix(".jxl")

    raise ValueError(f"Modo inválido: {mode}")

def extract_exif_raw(tiff_path, tmp_dir):
    arg_file = tmp_dir / "exif_extract.args"
    arg_file.write_text(f"-b\n-Exif\n{tiff_path}\n", encoding="utf-8")
    r = subprocess.run(["exiftool", "-@", str(arg_file)], capture_output=True)
    if r.returncode == 0 and len(r.stdout) > 8:
        p = tmp_dir / f"{tiff_path.stem}.exif.bin"
        p.write_bytes(r.stdout)
        return p
    return None

def extract_icc_fixed(tiff_path):
    """Extracts ICC profile and patches D50 illuminant rounding error (Capture One non-conformance).
    Safe for any ICC profile: if bytes are already correct, the patch has no effect.
    Returns patched ICC bytes or None."""
    with tempfile.TemporaryDirectory(prefix="icc_", dir=TEMP_DIR) as tmp:
        arg_file = Path(tmp) / "icc_extract.args"
        arg_file.write_text(f"-b\n-ICC_Profile\n{tiff_path}\n", encoding="utf-8")
        r = subprocess.run(["exiftool", "-@", str(arg_file)], capture_output=True)
    if r.returncode == 0 and len(r.stdout) > 128:
        icc = bytearray(r.stdout)
        icc[68:80] = bytes.fromhex("0000f6d6000100000000d32d")  # fix D50 illuminant
        return bytes(icc)
    return None

def make_png_bytes(img, icc_bytes=None):
    """Encodes a 16-bit numpy array as PNG in memory (pure Python, no temp file)."""
    h, w, c = img.shape
    color_type = 2 if c == 3 else (0 if c == 1 else 6)

    def chunk(name, data):
        p = name + data
        return struct.pack(">I", len(data)) + p + struct.pack(">I", zlib.crc32(p) & 0xFFFFFFFF)

    img_be = img.astype(">u2")
    raw = b"".join(b"\x00" + row.tobytes() for row in img_be)

    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, color_type, 0, 0, 0))
    if icc_bytes:
        out += chunk(b"iCCP", b"ICC Profile\x00\x00" + zlib.compress(icc_bytes))
    out += chunk(b"IDAT", zlib.compress(raw, 1))
    out += chunk(b"IEND", b"")
    return out

def reorder_jxl_boxes(jxl_path):
    """Reorders ISOBMFF boxes so Exif comes BEFORE the codestream.
    IrfanView reads JXL boxes linearly and stops at the codestream — Exif must come first.
    Supports both lossless (single jxlc) and lossy (multiple jxlp) JXL."""
    data = jxl_path.read_bytes()
    boxes = []
    i = 0
    while i < len(data):
        if i + 8 > len(data): break
        size = int.from_bytes(data[i:i+4], "big")
        name = data[i+4:i+8]
        if size == 1:
            size = int.from_bytes(data[i+8:i+16], "big")
            header, payload = data[i:i+16], data[i+16:i+size]
        elif size == 0:
            header, payload = data[i:i+8], data[i+8:]
            boxes.append((name, header, payload))
            break
        else:
            header, payload = data[i:i+8], data[i+8:i+size]
        boxes.append((name, header, payload))
        i += size if size != 0 else len(data)

    # Separa boxes em grupos: metadados (antes do codestream) e codestream
    CODESTREAM = {b"jxlc", b"jxlp"}
    META_ORDER = [b"JXL ", b"ftyp", b"jxll"]  # estrutura obrigatória primeiro
    META_EXTRA = [b"Exif", b"xml "]            # metadados que queremos antes do codestream

    # Agrupa por tipo preservando ordem de aparição (importante para múltiplos jxlp)
    meta_order_boxes  = []
    meta_extra_boxes  = []
    codestream_boxes  = []
    other_boxes       = []

    for name, h, p in boxes:
        if name in {b"JXL ", b"ftyp", b"jxll"}:
            meta_order_boxes.append((name, h, p))
        elif name in {b"Exif", b"xml "}:
            meta_extra_boxes.append((name, h, p))
        elif name in CODESTREAM:
            codestream_boxes.append((name, h, p))
        else:
            other_boxes.append((name, h, p))

    # Ordem final: estrutura → metadados → codestream → resto
    out = b""
    for _, h, p in meta_order_boxes:  out += h + p
    for _, h, p in meta_extra_boxes:  out += h + p
    for _, h, p in codestream_boxes:  out += h + p
    for _, h, p in other_boxes:       out += h + p

    jxl_path.write_bytes(out)

def convert_one(tiff_path: Path, write_path: Path, final_path: Path):
    """
    Converts a single TIFF to JXL.
    write_path: where the JXL is initially written (staging or final destination)
    final_path: the final destination path (for overwrite checking and logging)
    """
    overwritten = final_path.exists()

    if overwritten:
        if OVERWRITE == False:
            n, total = next_count()
            logger.info(f"[{n}/{total}] SKIP (exists) | {tiff_path.name}")
            return (str(tiff_path), "skipped", str(final_path))
        elif OVERWRITE == "smart":
            tiff_mtime = tiff_path.stat().st_mtime
            jxl_mtime  = final_path.stat().st_mtime
            if tiff_mtime <= jxl_mtime:
                n, total = next_count()
                logger.info(f"[{n}/{total}] SKIP (sync: JXL up to date) | {tiff_path.name}")
                return (str(tiff_path), "skipped", str(final_path))
            logger.info(f"  → SYNC: TIFF newer than JXL, reconverting | {tiff_path.name}")

    write_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="jxl_", dir=TEMP_DIR) as tmp:
        tmp_dir = Path(tmp)
        try:
            # 1. Extract raw EXIF binary
            exif_bin = extract_exif_raw(tiff_path, tmp_dir)

            # 2. Extract and patch ICC profile
            icc_bytes = extract_icc_fixed(tiff_path)

            # 3. Read TIFF pixel data (series[0] = main image, ignores thumbnails)
            with tifffile.TiffFile(str(tiff_path)) as tif:
                img = tif.series[0].asarray().astype(np.uint16)
            if img.ndim == 2:
                img = img[:, :, np.newaxis]

            # 4. Encode PNG and pass to cjxl
            # --container=1 is required for lossy JXL (d>0): without it, cjxl outputs a raw
            # codestream and exiftool cannot inject EXIF. Do NOT use for lossless (d=0):
            # it changes how the ICC is stored (blob instead of native primaries) and
            # breaks color display in IrfanView.
            container_flag = ["--container=1"] if CJXL_DISTANCE > 0 else []

            if USE_RAM_FOR_PNG:
                png_input = make_png_bytes(img, icc_bytes)
                del img
                cjxl_cmd = ["cjxl", "-", str(write_path), "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT)] + container_flag
                r = subprocess.run(cjxl_cmd, input=png_input, capture_output=True)
                del png_input
            else:
                png_path = tmp_dir / f"{tiff_path.stem}.png"
                png_bytes = make_png_bytes(img, icc_bytes)
                del img
                png_path.write_bytes(png_bytes)
                del png_bytes
                cjxl_cmd = ["cjxl", str(png_path), str(write_path), "-d", str(CJXL_DISTANCE), "--effort", str(CJXL_EFFORT)] + container_flag
                r = subprocess.run(cjxl_cmd, capture_output=True)

            if r.returncode != 0:
                raise RuntimeError(f"cjxl: {r.stderr.decode(errors='replace')[:200]}")

            # 5. Inject EXIF + XMP using exiftool -@ argument file (avoids bracket wildcard issues)
            arg_inject = tmp_dir / "inject.args"

            # Build optional encoding tag line
            encode_tag_line = ""
            if ENCODE_TAG_MODE == "xmp":
                encode_tag_line = f"-XMP-dc:Description=cjxl d={CJXL_DISTANCE} e={CJXL_EFFORT}\n"
            elif ENCODE_TAG_MODE == "software":
                # Read original Software field from TIFF and append encoding params
                r_sw = subprocess.run(
                    ["exiftool", "-@", str(arg_inject.parent / "sw_read.args")],
                    capture_output=True, text=True
                )
                
                sw_arg = tmp_dir / "sw_read.args"
                sw_arg.write_text(f"-s\n-s\n-s\n-Software\n{tiff_path}\n", encoding="utf-8")
                r_sw = subprocess.run(["exiftool", "-@", str(sw_arg)], capture_output=True, text=True)
                original_sw = r_sw.stdout.strip() if r_sw.returncode == 0 and r_sw.stdout.strip() else "cjxl"
                new_sw = f"{original_sw} | cjxl d={CJXL_DISTANCE} e={CJXL_EFFORT}"
                encode_tag_line = f"-Software={new_sw}\n"

            if exif_bin:
                arg_inject.write_text(
                    f"-overwrite_original\n-Exif<={exif_bin}\n-tagsfromfile\n{tiff_path}\n"
                    f"-xmp:all\n--Orientation\n{encode_tag_line}{write_path}\n",
                    encoding="utf-8"
                )
            else:
                arg_inject.write_text(
                    f"-overwrite_original\n-tagsfromfile\n{tiff_path}\n"
                    f"-exif:all\n-xmp:all\n--Orientation\n-ExifByteOrder=Little-endian\n{encode_tag_line}{write_path}\n",
                    encoding="utf-8"
                )
            r2 = subprocess.run(["exiftool", "-@", str(arg_inject)], capture_output=True, text=True)
            if r2.returncode != 0:
                err_msg = (r2.stderr or r2.stdout or "no output")[:300].strip()
                raise RuntimeError(f"exiftool failed: {err_msg}")

            # 6. Reorder JXL boxes so Exif comes before the codestream
            reorder_jxl_boxes(write_path)

            n, total = next_count()
            status = "overwrite" if overwritten else "ok"
            label  = "OVERWRITE" if overwritten else "OK"
            logger.info(f"[{n}/{total}] {label} | {tiff_path.name} → {final_path}")
            return (str(tiff_path), status, str(final_path))

        except Exception as e:
            n, total = next_count()
            logger.error(f"[{n}/{total}] ERROR | {tiff_path.name} | {e}")
            return (str(tiff_path), "error", str(e))

def process_group(group_pairs: list, workers: int):
    """
    Converts a group of (tiff, final_jxl) pairs in parallel.
    If TEMP2_DIR is set, writes to staging first then moves in bulk.
    """
    use_staging = TEMP2_DIR is not None
    staging_dir = Path(TEMP2_DIR) if use_staging else None

    if use_staging:
        staging_dir.mkdir(parents=True, exist_ok=True)

    
    tasks = []
    for tiff, final_jxl in group_pairs:
        if use_staging:
            # Unique staging name to avoid collisions across different source folders
            write_jxl = staging_dir / f"{tiff.parent.name}__{tiff.stem}.jxl"
        else:
            write_jxl = final_jxl
        tasks.append((tiff, write_jxl, final_jxl))

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(convert_one, t, w, f): (t, w, f) for t, w, f in tasks}
        for fut in as_completed(futures):
            results.append(fut.result())

    # Move from staging to final destination in bulk
    if use_staging:
        moved = 0
        for tiff, write_jxl, final_jxl in tasks:
            if write_jxl.exists():
                final_jxl.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(write_jxl), str(final_jxl))
                moved += 1
        if moved:
            logger.info(f"  → Moved {moved} file(s) from staging to final destination")

    return results

def find_files_mode0(input_path: Path):
    seen = set()
    files = []
    for ext in ("*.jpg", "*.jpeg", "*.tif", "*.tiff"):
        for f in input_path.glob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

def find_tiffs_recursive(input_path: Path):
    seen = set()
    files = []
    for ext in ("*.tif", "*.tiff"):
        for f in input_path.rglob(ext):
            key = f.resolve()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files

def find_tiffs_mode5(input_path: Path):
    """Mode 5: only TIFFs inside folders containing EXPORT_MARKER in their path."""
    all_tiffs = find_tiffs_recursive(input_path)
    filtered = []
    for t in all_tiffs:
        parts_str = [p for p in t.parts]
        
        export_idx = next((i for i, p in enumerate(parts_str) if EXPORT_MARKER in p), None)
        if export_idx is None:
            continue
        
        if EXPORT_TIFF_SUBFOLDER:
            
            if export_idx + 1 < len(parts_str) and parts_str[export_idx + 1] == EXPORT_TIFF_SUBFOLDER:
                filtered.append(t)
        else:
            filtered.append(t)
    return filtered

def main():
    parser = argparse.ArgumentParser(description="Batch TIFF 16-bit → JPEG XL converter")
    parser.add_argument("input",             type=Path, help="Input root folder")
    parser.add_argument("output", nargs="?", type=Path, help="Output folder (mode 0 only)")
    parser.add_argument("--mode",            type=int, default=0, choices=[0,1,2,3,4,5])
    parser.add_argument("--workers",         type=int, default=min(os.cpu_count(), 16))
    parser.add_argument("--overwrite",       action="store_true",
                        help="Always overwrite existing JXLs")
    parser.add_argument("--sync",            action="store_true",
                        help="Only reconvert TIFFs newer than their existing JXL")
    args = parser.parse_args()

    global OVERWRITE
    if args.sync:
        OVERWRITE = "smart"
    elif args.overwrite:
        OVERWRITE = True

    log_file = setup_logger()
    logger.info(
        f"Mode: {args.mode} | Effort: {CJXL_EFFORT} | "
        f"Distance: {CJXL_DISTANCE} ({'lossless' if CJXL_DISTANCE == 0 else 'lossy'}) | "
        f"RAM PNG: {USE_RAM_FOR_PNG} | Staging: {TEMP2_DIR or 'disabled'} | "
        f"Overwrite: {'sync (smart)' if args.sync else OVERWRITE} | Workers: {args.workers}"
    )
    logger.info(f"Input: {args.input}")

    # Collect input files
    if args.mode == 0:
        tiffs = find_files_mode0(args.input)
        output_root = args.output or args.input
    elif args.mode == 5:
        tiffs = find_tiffs_mode5(args.input)
        output_root = args.input
    else:
        tiffs = find_tiffs_recursive(args.input)
        output_root = args.input

    logger.info(f"Files found: {len(tiffs)}")
    _counter["total"] = len(tiffs)

    # Build (tiff, jxl_destination) pairs
    pairs = []
    for t in tiffs:
        jxl = output_root / t.with_suffix(".jxl").name if args.mode == 0 else resolve_output(t, args.mode, args.input)
        pairs.append((t, jxl))

    # Group by output folder (one bulk move per group)
    groups: dict[Path, list] = {}
    for t, j in pairs:
        groups.setdefault(j.parent, []).append((t, j))

    logger.info(f"Output groups: {len(groups)}")

    ok = err = skipped = overwritten = synced = 0

    for dest_folder, group_pairs in groups.items():
        if len(groups) > 1:
            logger.info(f"── Group: {dest_folder} ({len(group_pairs)} file(s))")

        results = process_group(group_pairs, args.workers)

        for _, status, _ in results:
            if   status == "ok":        ok += 1
            elif status == "overwrite": ok += 1; overwritten += 1; synced += 1
            elif status == "skipped":   skipped += 1
            elif status == "error":     err += 1

    logger.info(f"\n{'─'*50}")
    if args.sync:
        logger.info(f"SYNC done: {synced} reconverted | {skipped} up to date | {err} errors")
        logger.info(f"  → Reconverted: TIFFs newer than their existing JXL")
        logger.info(f"  → Up to date: JXL is newer than or equal to TIFF")
    else:
        logger.info(f"Done: {ok} OK | {overwritten} overwrites | {skipped} skipped | {err} errors")
    logger.info(f"Log: {log_file}")

if __name__ == "__main__":
    main()
