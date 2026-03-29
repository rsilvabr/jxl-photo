# jxl_to_tiff.py

Batch JPEG XL → TIFF 16-bit converter. Decodes JXL files back to editable TIFF format
while preserving all metadata (EXIF, XMP, ICC color profiles).

Works with any JXL file — lossless (`d=0`) or lossy (`d>0`) — produced by cjxl, 
Capture One exports, or any libjxl-based workflow.

#### Key feature:
```
 Full ICC profile preservation — when used with `tiff_to_jxl.py`, 
the exact original ICC profile (with gamma curves, copyright, etc.) is embedded in 
the JXL as XMP metadata and restored on round-trip conversion. This is especially
important for **lossy JXL files**, which otherwise would use a generic ICC 
generated from primaries (see [ICC Preservation](#icc-profile-preservation) below).
```
---

## Disclaimer

These tools were made for my personal workflow (with the help of Claude). Use at your own risk — I am not responsible for any issues you may encounter.

---

## Requirements

```
Python 3.12+
pip install tifffile numpy
djxl  →  https://github.com/libjxl/libjxl/releases
exiftool  →  https://exiftool.org
```

Both `djxl.exe` and `exiftool.exe` must be on your PATH.

Quick way to add them (run in PowerShell, then reopen the terminal):
```powershell
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", "$p;C:\tools\libjxl\bin;C:\tools\exiftool", "User")
```

Verify:
```powershell
djxl --version      # JPEG XL decoder v0.11.x
exiftool -ver       # 13.xx
```

---

## Quick start

```powershell
# ── The easy way — mode 0, no flags needed ──────────────────────
# Single file, in-place
py jxl_to_tiff.py "F:\Photos\photo.jxl"

# Single file → specific output folder
py jxl_to_tiff.py "F:\Photos\photo.jxl" "F:\output"

# Whole folder, in-place (flat — subfolders not touched)
py jxl_to_tiff.py "F:\Photos"

# Whole folder → specific output folder (flat)
py jxl_to_tiff.py "F:\Photos" "F:\output"

# ── Other modes ──────────────────────────────────────────────────
# Capture One _EXPORT workflow (mode 7) — most common for C1 users
py jxl_to_tiff.py "F:\2024" --mode 7

# Sync — only reconvert JXLs newer than existing TIFF
py jxl_to_tiff.py "F:\2024" --mode 7 --sync

# 16 parallel workers
py jxl_to_tiff.py "F:\2024" --mode 7 --workers 16

# Output 8-bit instead of 16-bit (smaller files, web delivery)
py jxl_to_tiff.py "F:\Photos" --depth 8

# Mode 8 — in-place recursive: TIFF next to each JXL, all subfolders
py jxl_to_tiff.py "F:\2024" --mode 8

# Use LZW compression instead of ZIP (faster, slightly larger files)
py jxl_to_tiff.py "F:\Photos" --compression lzw

# Uncompressed TIFF (fastest, largest files - for editing workflows)
py jxl_to_tiff.py "F:\Photos" --compression uncompressed
```

---

## Key settings

Edit at the top of the script:

```python
DJXL_OUTPUT_DEPTH = 16
# Output bit depth for TIFF (8 or 16).
# 16 is recommended for maximum quality preservation (especially for further editing).
# 8 can be used for web/delivery to save ~50% space.

TIFF_COMPRESSION = "zip"
# TIFF compression method. Options: "uncompressed", "lzw", "zip"
# "uncompressed" - No compression, largest files, fastest write
# "lzw"          - LZW compression, good compatibility, medium size  
# "zip"          - Deflate/ZIP compression (default), best compression

ADD_JPEG_PREVIEW = True
# Add an embedded JPEG preview/thumbnail to the TIFF file.
# This enables fast preview in file explorers (Windows Explorer, etc.)
# and image viewers without loading the full-resolution image.
# True  → Add JPEG preview (default, recommended)
# False → No preview, slightly smaller file

JPEG_PREVIEW_SIZE = 1024
# Maximum dimension (width or height) of the JPEG preview.
# Default: 1024 pixels. Larger = better preview quality, larger file.

TEMP2_DIR = r"E:\staging"
# Staging SSD for output TIFFs. Separates read I/O (HDD with JXLs) from write I/O.
# Files are moved to their final destination after each folder group completes.
# Set to None to write directly to the final destination.

OVERWRITE = "smart"
# False   → skip if TIFF already exists (safe for resuming)
# True    → always overwrite
# "smart" → same as --sync: reconvert only if JXL is newer than TIFF

DELETE_SOURCE = False
# [Modes 6/8 only] False → JXL and TIFF coexist (safe default)
# True  → delete source JXL after confirmed successful decode (irreversible)

# — Safety (DELETE_SOURCE only) —
DELETE_CONFIRM = True
# True  (default) → ask for confirmation before deleting any source JXL.
#   Type the current time in HHMM format shown on screen — this cannot be automated,
#   forcing a conscious decision before deleting files.
# False → skip confirmation (for automation). Not recommended for manual use.

# — Mode 1 only —
CONVERTED_TIFF_FOLDER = "converted_tiff"
# Name of the subfolder created inside each JXL folder.

# — Mode 3 only —
TIFF_FOLDER_NAME = "TIFF_16bits"
# Name of the output folder created next to each JXL folder.

# — Mode 4 only —
JXL_SUFFIX_TO_REPLACE = "JXL"
TIFF_SUFFIX_REPLACE     = "TIFF"
# Replaces JXL_SUFFIX_TO_REPLACE with TIFF_SUFFIX_REPLACE in the folder name.
# Case-insensitive. If not found, appends TIFF_SUFFIX_REPLACE and logs a warning.

# — Modes 6 and 7 —
EXPORT_MARKER     = "_EXPORT"    # anchor folder name to look for in the path
EXPORT_TIFF_FOLDER = "16B_TIFF"  # output folder created inside the anchor
EXPORT_JXL_SUBFOLDER = ""
# Mode 7: process only JXLs in this subfolder of _EXPORT.
# Set to "" to process all subfolders inside _EXPORT.
```

### Safety confirmation (DELETE_SOURCE)

When `DELETE_SOURCE = True` and `DELETE_CONFIRM = True`, the script asks for confirmation 
before deleting any source file. This happens once at startup, before any conversion begins.

The script shows the current time and asks you to type it in `HHMM` format. 
The intention is to create "friction" and force a conscious decision — 
you are about to delete JXLs that cannot be recovered if the decode had issues.

```
  ⚠  WARNING — DELETE_SOURCE is enabled
     Source JXLs will be deleted after successful decode.
     This deletion is IRREVERSIBLE.
     Current time: 14:23  →  to confirm, type: 1423

     > 1423
     Confirmed.
```

If anything other than the exact token is entered, the script exits without converting
or deleting anything.

Set `DELETE_CONFIRM = False` only if running the script from an automation pipeline
where interactive input is not possible. For any manual use, leave it `True` —
it takes 3 seconds and is much safer.


---

## Output modes

| Mode | Input | Output location | Example |
|------|-------|----------------|---------|
| `0` | File or folder | In-place or → output_dir (flat, non-recursive) | `photo.tif` / `output_dir/photo.tif` |
| `1` | Single file | `converted_tiff/` subfolder next to source | `.../converted_tiff/photo.tif` |
| `2` | Directory | Flat → output_dir | `output_dir/photo.tif` |
| `3` | Directory | `converted_tiff/` inside each JXL folder | `.../JXL/converted_tiff/photo.tif` |
| `4` | Directory | Rename folder `JXL` → `TIFF` | `.../Export_TIFF/photo.tif` |
| `5` | Directory | Sibling folder `TIFF_16bits/` | `.../TIFF_16bits/photo.tif` |
| `6` | Directory | `_EXPORT` anchor — all JXLs in hierarchy | `.../session/_EXPORT/16B_TIFF/photo.tif` |
| `7` | Directory | `_EXPORT` anchor — only JXLs inside `_EXPORT` | `.../session/_EXPORT/16B_TIFF/photo.tif` |
| `8` | Directory | In-place recursive — TIFF next to each JXL | `.../session/photo.tif` |

**Mode 7 example** with `EXPORT_JXL_SUBFOLDER = "JXL"`:
```
session/_EXPORT/JXL/photo.jxl      →  session/_EXPORT/16B_TIFF/photo.tif  ✓
session/_EXPORT/AdobeRGB/photo.jxl →  ignored
session/_EXPORT/sRGB/photo.jxl     →  ignored
```

---

## CLI reference

```
py jxl_to_tiff.py <input> [output] [options]

Arguments:
  input           Input root folder or file
  output          Output folder (mode 0 only)

Options:
  --mode 0-8          Output folder mode (default: 0)
  --workers N         Parallel threads (tested up to 32 on a Ryzen 9 5950X)
  --overwrite         Always overwrite existing TIFFs
  --sync              Reconvert only JXLs newer than their TIFF
  --depth 8|16        Output bit depth (overrides DJXL_OUTPUT_DEPTH setting)
  --compression TYPE  TIFF compression: uncompressed, lzw, or zip (default: zip)
```

---

## Performance

With `TEMP2_DIR` set to a separate SSD, TIFFs are written to fast storage during conversion
and moved in bulk at the end — eliminates random write contention on HDD collections.

Decoding is typically faster than encoding, as djxl is highly optimized.


---

## Logs

```
<script_folder>/Logs/jxl_to_tiff/YYYYMMDD_HHMMSS.log
```

Opening line shows all active settings. Each converted file logs:
```
HH:MM:SS | INFO | [n/total] OK | photo.jxl → F:\...\photo.tif
```

Final summary:
```
Done: 90 OK | 0 overwrites | 0 skipped | 0 errors
```

---

## Relationship with tiff_to_jxl.py

These scripts are designed to work as a pair:

```powershell
# Encode: TIFF → JXL
py tiff_to_jxl.py "photo.tif" --mode 0

# Decode: JXL → TIFF (round-trip)
py jxl_to_tiff.py "photo.jxl" --mode 0
```

**Lossless round-trip:**
- TIFF → JXL (d=0) → TIFF produces **bit-identical** pixel data
- All metadata and ICC profiles are preserved
- File size returns to original (modulo TIFF compression differences)

**Lossy round-trip:**
- TIFF → JXL (d>0) → TIFF produces **visually similar** but not identical pixels
- Further round-trips accumulate generational loss — avoid multiple lossy conversions

---

## ICC Profile Preservation

### The Problem with Lossy JXL

When you convert TIFF → **lossy JXL** (`d>0`), the JXL encoder converts the ICC color profile
to **native primaries** for efficient encoding:

```
Original TIFF: ICC ProPhoto RGB (Kodak, with TRC curves, copyright, etc.)
     ↓ cjxl (lossy)
JXL file: Native primaries (red=0.7347,0.2653; green=0.1596,0.8404...)
     ↓ djxl decode
TIFF without ICC preservation: Generic ICC generated from primaries (no TRC detail)
```

The generic ICC **works** for display, but lacks:
- **Tone Reproduction Curves (TRC)**: Precise gamma curves for shadow/highlight handling
- **Copyright and metadata**: Original profile attribution (Kodak, Adobe, etc.)
- **Device-specific calibration**: Perceptual/relative colorimetric intents

For professional editing workflows, these details matter!

### The Solution: XMP-Embedded ICC

When using `tiff_to_jxl.py` with `EMBED_ICC_IN_JXL = True` (default):

1. The **original ICC profile** is extracted from the TIFF
2. It's **base64-encoded** and embedded in XMP metadata
3. The JXL still uses native primaries for encoding (efficient)
4. On conversion back, the **exact original ICC** is restored

```
TIFF with ProPhoto ICC
     ↓ tiff_to_jxl.py (EMBED_ICC_IN_JXL = True)
JXL: Native primaries + XMP(dc:Description="ICC:AAADrE...")
     ↓ jxl_to_tiff.py
TIFF: Original ProPhoto ICC restored (Kodak TRC, copyright, etc.)
```

### Why This Matters

| Aspect | Generic ICC (from primaries) | Original ICC (preserved) |
|--------|-------------------------------|--------------------------|
| **Visual display** | ✅ Correct colors | ✅ Correct colors |
| **Professional editing** | ⚠️ Slight gamma inaccuracies | ✅ Precise TRC curves |
| **Color-critical work** | ⚠️ May drift in conversions | ✅ Stable colorimetry |
| **Archive value** | ⚠️ Loses original profile info | ✅ Full metadata preserved |

For most users viewing photos, the difference is negligible. For photographers 
doing heavy shadow recovery, print calibration, or color grading, the original 
ICC provides more accurate and predictable results.

→ See [JXL Color Internals](jxl_color_internals.md) for technical deep-dive.

---

## JPEG Preview / Thumbnail

When `ADD_JPEG_PREVIEW = True` (default), the TIFF file includes a second page 
with a JPEG-compressed preview image. This enables:

- **Fast thumbnail generation** in Windows Explorer / Finder
- **Quick preview** in image viewers without loading full 16-bit data
- **Compatibility** with older software that expects embedded previews

The preview is stored as a **separate page** (subIFD) in the TIFF:
```
Page 0: 7195x4802 @ 16-bit (main image, ZIP compressed)
Page 1: 1024x683 @ 8-bit (preview, JPEG compressed)
```

**To disable:** Set `ADD_JPEG_PREVIEW = False` at the top of the script.
**To adjust size:** Change `JPEG_PREVIEW_SIZE` (default: 1024 pixels max dimension).

---

## Appendix: How to verify output

Check the decoded TIFF has correct colorspace and metadata:

```powershell
# Show all EXIF and XMP metadata
exiftool photo.tif

# Show ICC color profile
exiftool -ICC_Profile photo.tif

# Check for JPEG preview (multiple pages)
exiftool photo.tif | findstr /C:"Page" /C:"Image Count"

# Show basic image properties (dimensions, bit depth, etc.)
identify photo.tif        # ImageMagick
# or
exiftool -ImageSize -BitDepth -ColorSpace photo.tif
```

Compare with original JXL info:
```powershell
jxlinfo photo.jxl
```

---

## Known limitations

- **Metadata extraction**: Some exotic JXL metadata formats may not transfer perfectly to TIFF. Standard EXIF/XMP/ICC from cjxl/libjxl work correctly.
- **Animation**: JXL animations decode to the first frame only (TIFF doesn't support animation).
- **Grayscale with alpha**: May produce RGB TIFF depending on djxl version. Use `--depth 8` for smaller files if alpha is not needed.

---

## Disclaimer

These tools were made for my personal workflow (with the help of Claude). Use at your own risk — I am not responsible for any issues you may encounter.

---

## License

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation  
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling  
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O  
---

### Development Assistance
- [Kimi](https://www.moonshot.cn) (Moonshot AI) and Claude (Anthropic) for code assistance and technical discussion
