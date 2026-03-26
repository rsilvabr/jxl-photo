# tiff_to_jxl.py

> **Note — mode numbering changed 2026-03-26.**
> Modes 0–5 were renumbered to 0–8 to accommodate two new single-file modes (0 and 1).
> If you have saved commands or scripts using `--mode`, update them accordingly.
> Old → new: `0→2`, `1→3`, `2→5`, `3→4`, `4→6`, `5→7`, `6→8`.

Batch TIFF 16-bit → JPEG XL converter. Supports lossless (`d=0`) and lossy (`d>0`),
preserves full EXIF visible in IrfanView, and keeps any embedded ICC color profile intact.

Works with any 16-bit TIFF — not limited to Capture One exports.
Tested with Capture One, NX Studio, and standard uncompressed TIFFs from various sources.

---

## Disclaimer

These tools were made for my personal workflow (with the help of Claude). Use at your own risk — I am not responsible for any issues you may encounter.

---

## Requirements

```
Python 3.12+
pip install tifffile numpy
cjxl  →  https://github.com/libjxl/libjxl/releases
exiftool  →  https://exiftool.org
```

Both `cjxl.exe` and `exiftool.exe` must be on your PATH.

Quick way to add them (run in PowerShell, then reopen the terminal):
```powershell
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", "$p;C:\tools\libjxl\bin;C:\tools\exiftool", "User")
```

Verify:
```powershell
cjxl --version      # JPEG XL encoder v0.11.x
exiftool -ver       # 13.xx
```

---

## Quick start

```powershell
# ── The easy way — mode 0, no flags needed ──────────────────────
# Single file, in-place
py tiff_to_jxl.py "F:\Photos\photo.tif"

# Single file → specific output folder
py tiff_to_jxl.py "F:\Photos\photo.tif" "F:\output"

# Whole folder, in-place (flat — subfolders not touched)
py tiff_to_jxl.py "F:\Photos"

# Whole folder → specific output folder (flat)
py tiff_to_jxl.py "F:\Photos" "F:\output"

# ── Other modes ──────────────────────────────────────────────────
# Capture One _EXPORT workflow (mode 7) — most common for C1 users
py tiff_to_jxl.py "F:\2024" --mode 7

# Sync — only reconvert TIFFs newer than existing JXL
py tiff_to_jxl.py "F:\2024" --mode 7 --sync

# 16 parallel workers
py tiff_to_jxl.py "F:\2024" --mode 7 --workers 16

# Mode 8 — in-place recursive: JXL next to each TIFF, all subfolders
py tiff_to_jxl.py "F:\2024" --mode 8
```

---

## Key settings

Edit at the top of the script:

```python
CJXL_DISTANCE = 0.0
# 0   = lossless (pixel-perfect, ~173MB for 45MP)
# 0.05 = near-lossless (~47MB, imperceptible difference)
# 0.1 = near-lossless (~34MB, imperceptible difference)
# 0.5 = high quality lossy (~13MB) — recommended starting point
# 1.0 = "visually lossless" per libjxl documentation (~8MB)

CJXL_EFFORT = 7
# Controls file size, NOT quality. 7 is the sweet spot for camera photos.
# Effort 8-10 is much slower and can produce larger files for high-ISO images.

CJXL_MODULAR = False
# False (default) — lossy uses VarDCT encoder + XYB colorspace.
#   This is the standard lossy mode: DCT-based, like JPEG but much more advanced.
#   Compresses photo content very efficiently. File sizes as shown in the table above.
#
# True — forces Modular encoder for lossy (--modular=1).
#   Modular is entropy-coded (similar to FLIF/PNG). It is the only encoder used
#   for lossless, but is significantly less efficient for lossy photo content.
#   Good for UI/screenshots, text, pixel art and rasterized vector graphics.
#   Note: lossless (d=0) always uses Modular regardless of this setting.

TEMP2_DIR = r"E:\staging"
# Staging SSD for output JXLs. Separates read I/O (HDD with TIFFs) from write I/O.
# Files are moved to their final destination after each folder group completes.
# Set to None to write directly to the final destination.

EXPORT_TIFF_SUBFOLDER = "TIFF16"
# Mode 5: process only TIFFs in this subfolder of _EXPORT.
# Prevents accidentally converting AdobeRGB, sRGB, or WEB exports.
# Set to "" to process all subfolders inside _EXPORT.

ENCODE_TAG_MODE = "xmp"
# "software" → appends "| cjxl d=X e=Y" to the EXIF Software field
# "xmp"      → writes as XMP-dc:Description
# "off"      → no encoding tag

OVERWRITE = "smart"
# False   → skip if JXL already exists (safe for resuming)
# True    → always overwrite
# "smart" → same as --sync: reconvert only if TIFF is newer than JXL

# — Mode 1 only —
CONVERTED_JXL_FOLDER = "converted_jxl"
# Name of the subfolder created inside each TIFF folder.

# — Mode 2 only —
JXL_FOLDER_NAME = "JXL_16bits"
# Name of the output folder created next to each TIFF folder.

# — Mode 3 only —
TIFF_SUFFIX_TO_REPLACE = "TIFF"
JXL_SUFFIX_REPLACE     = "JXL"
# Replaces TIFF_SUFFIX_TO_REPLACE with JXL_SUFFIX_REPLACE in the folder name.
# Case-insensitive. If not found, appends JXL_SUFFIX_REPLACE and logs a warning.

# — Modes 4 and 5 —
EXPORT_MARKER     = "_EXPORT"   # anchor folder name to look for in the path
EXPORT_JXL_FOLDER = "16B_JXL"  # output folder created inside the anchor

# — Mode 6 —
DELETE_SOURCE = False
# False → JXL and TIFF coexist in the same folder (safe default)
# True  → delete source TIFF after confirmed successful encode (irreversible)

# — Safety (mode 6 + DELETE_SOURCE only) —
DELETE_CONFIRM = True
# True  (default) → ask for confirmation before deleting any source TIFF.
#   Lossless: type "yes".
#   Lossy: type the current time in HHMM format shown on screen — this cannot
#          be automated, forcing a conscious decision before deleting files that
#          cannot be recovered from a lossy JXL.
# False → skip confirmation (for automation). Not recommended for manual use.
#
# Leave this True. Disabling it means one misconfigured run can silently
# delete originals with no warning.
```

---

## Output modes

| Mode | Input | Output location | Example |
|------|-------|----------------|---------|
| `0` | File or folder | In-place or → output_dir (flat, non-recursive) | `photo.jxl` / `output_dir/photo.jxl` |
| `1` | Single file | `converted_jxl/` subfolder next to source | `.../converted_jxl/photo.jxl` |
| `2` | — | *Discontinued — use mode 0 with output_dir* | — |
| `3` | Directory | `converted_jxl/` inside each TIFF folder | `.../TIFF/converted_jxl/photo.jxl` |
| `4` | Directory | Rename folder `TIFF` → `JXL` | `.../Export_JXL/photo.jxl` |
| `5` | Directory | Sibling folder `JXL_16bits/` | `.../JXL_16bits/photo.jxl` |
| `6` | Directory | `_EXPORT` anchor — all TIFFs in hierarchy | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `7` | Directory | `_EXPORT` anchor — only TIFFs inside `_EXPORT` | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `8` | Directory | In-place recursive — JXL next to each TIFF | `.../session/photo.jxl` |

**Mode 7 example** with `EXPORT_TIFF_SUBFOLDER = "TIFF16"`:
```
session/_EXPORT/TIFF16/photo.tif      →  session/_EXPORT/16B_JXL/photo.jxl  ✓
session/_EXPORT/AdobeRGB/photo.tif    →  ignored
session/_EXPORT/sRGB/photo.tif        →  ignored
```

---

## CLI reference

```
py tiff_to_jxl.py <input> [output] [options]

Arguments:
  input           Input root folder
  output          Output folder (mode 0 only)

Options:
  --mode 0-8      Output folder mode (default: 0)
  --workers N     Parallel threads (tested up to 32 on a Ryzen 9 5950X)
  --overwrite     Always overwrite existing JXLs
  --sync          Reconvert only TIFFs newer than their JXL
```

---

## Further reading

For a deep dive into how JXL handles color management internally — XYB vs non-XYB, ICC blobs, CICP encoding, box structure, and how to verify your files with `jxlinfo` — see:

→ [JXL Color Internals](docs/jxl_color_internals.md)

---

## Performance

With `USE_RAM_FOR_PNG = True` (default), disk I/O per file = read TIFF + write JXL.
The PNG intermediate (~200MB) lives entirely in RAM.

With `TEMP2_DIR` set to a separate SSD, JXLs are written to fast storage during conversion
and moved in bulk at the end — eliminates random write contention on HDD collections.

---

## Safety confirmation (mode 6 + DELETE_SOURCE)

When `DELETE_SOURCE = True` and `DELETE_CONFIRM = True` (both defaults for their
respective concerns), the script asks for confirmation before deleting any source file.
This happens once at startup, before any conversion begins.

For **lossless** conversion, type `yes` to confirm.

For **lossy** conversion, the script shows the current time and asks you to type it
in `HHMM` format. The intention is to create "friction" and force a conscious decision — 
you are about to delete TIFFs that cannot be recovered from a lossy JXL.

```
  ⚠  WARNING — DELETE_SOURCE is enabled
     Converting LOSSY (distance=1.0) — source TIFFs cannot be
     recovered from a lossy JXL. This deletion is IRREVERSIBLE.
     Current time: 14:23  →  to confirm, type: 1423

     > 1423
     Confirmed.
```

If anything other than the exact token is entered, the script exits without converting
or deleting anything.

Set `DELETE_CONFIRM = False` only if running the script from an automation pipeline
where interactive input is not possible. For any manual use, leave it `True` —
it takes 3 seconds and is much safer.

## Logs

```
<script_folder>/Logs/tiff_to_jxl/YYYYMMDD_HHMMSS.log
```

Opening line shows all active settings. Each converted file logs:
```
HH:MM:SS | INFO | [n/total] OK | photo.tif → F:\...\photo.jxl
```

Final summary:
```
Done: 90 OK | 0 overwrites | 0 skipped | 0 errors
```

---

## Known behavior — XnView MP color profile display for lossy JXL

XnView MP shows `Color Profile: sRGB` in the properties panel for lossy JXL files,
even when the actual colorspace is ProPhoto RGB or AdobeRGB.

**This is not a conversion to sRGB.** It is a display error in XnView MP's metadata panel.

Lossy JXL encodes colorspace information as compact numeric primaries (CICP-style),
not as an embedded ICC blob. XnView reads the ICC blob field, finds nothing, and
falls back to showing "sRGB" as a default label.

The actual colorspace is correctly preserved and correctly rendered — as confirmed
by `jxlinfo` (see below) and by every other viewer (GIMP, Darktable, browsers, etc.).

To verify the real colorspace of any lossy JXL, use `jxlinfo` and check the
white_point and primary coordinates.

→ [How JXL color management works, how to verify your files, and a full primary coordinates reference table](docs/jxl_color_internals.md)

---

## Known behavior — IrfanView and color-calibrated monitors

JXL lossless files embed the ICC color profile as a blob. Most software handles this
correctly — GIMP, XnView MP, Darktable, Firefox, Waterfox, and `jxl_to_jpeg.py` all
display correct colors.

IrfanView's behavior with lossless JXL appears to depend on the system display profile
installed on the machine. In my testing, it worked correctly on an uncalibrated monitor.
After hardware calibration with an Eizo monitor, IrfanView stopped showing correct colors
for lossless JXL while continuing to show correct colors for lossy JXL. The cause appears
to be double color management — IrfanView applying both the embedded ICC and the system
display profile simultaneously.

Lossy JXL (`d > 0`) uses native JXL color primaries instead of an ICC blob and is not
affected by this issue.

**The files themselves are correct.** Any conformant JXL decoder will display the colors
accurately. The issue is specific to IrfanView on calibrated systems.

If lossless JXL colors look wrong in IrfanView, use lossy at `d=0.1` (imperceptible
difference, ~34MB for 45MP), or open the files in any of the viewers listed above.
Files also convert correctly to JPEG using `jxl_to_jpeg.py`.

---

## Appendix: How to verify output with jxlinfo

`jxlinfo` (included with the libjxl release) reports what the decoder actually sees —
colorspace primaries, bit depth, lossless vs lossy, and container structure.

```powershell
jxlinfo photo.jxl
```

**Example output for a ProPhoto RGB lossy file:**
```
JPEG XL file format container (ISO/IEC 18181-2)
Uncompressed Exif metadata: 892 bytes
Uncompressed xml  metadata: 4453 bytes
JPEG XL image, 1200x801, lossy, 16-bit RGB
Color space: RGB, Custom,
  white_point(x=0.345705, y=0.358540),
  Custom primaries:
    red(x=0.734698, y=0.265302),
    green(x=0.159600, y=0.840399),
    blue(x=0.036597, y=0.000106)
  gamma(0.555315) transfer function,
  rendering intent: Perceptual
```

**How to read the colorspace fields:**

| Field | ProPhoto value | sRGB value |
|-------|---------------|------------|
| white_point x,y | 0.3457, 0.3585 (D50) | 0.3127, 0.3290 (D65) |
| red primary x | ~0.7347 | 0.6400 |
| green primary x | ~0.1596 | 0.3000 |
| blue primary x | ~0.0366 | 0.1500 |
| gamma | 0.5556 (= 1/1.8) | 0.4545 (= 1/2.2) |

The white point and primaries are the definitive source of truth for the colorspace —
not the ICC profile description shown by viewers.

**Checking for EXIF and box structure:**

```powershell
# Show internal JXL box order (useful for debugging EXIF visibility)
exiftool -v3 photo.jxl

# Show all readable EXIF fields
exiftool photo.jxl
```
