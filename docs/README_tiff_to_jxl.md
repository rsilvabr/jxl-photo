# tiff_to_jxl.py

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
# Flat folder (mode 0)
py tiff_to_jxl.py "F:\input" "F:\output"

# Capture One _EXPORT workflow (mode 5) — most common
py tiff_to_jxl.py "F:\2024" --mode 5

# Sync — only reconvert TIFFs newer than existing JXL
py tiff_to_jxl.py "F:\2024" --mode 5 --sync

# 16 parallel workers
py tiff_to_jxl.py "F:\2024" --mode 5 --workers 16
```

---

## Key settings

Edit at the top of the script:

```python
CJXL_DISTANCE = 0.0
# 0   = lossless (pixel-perfect, ~173MB for 45MP)
# 0.1 = near-lossless (~34MB, imperceptible difference)
# 0.5 = high quality lossy (~13MB) — recommended starting point
# 1.0 = "visually lossless" per libjxl documentation (~8MB)

CJXL_EFFORT = 7
# Controls file size, NOT quality. 7 is the sweet spot for camera photos.
# Effort 8-10 is much slower and can produce larger files for high-ISO images.

TEMP2_DIR = r"E:\staging"
# Staging SSD for output JXLs. Separates read I/O (HDD with TIFFs) from write I/O.
# Files are moved to their final destination after each folder group completes.
# Set to None to write directly to the final destination.

EXPORT_TIFF_SUBFOLDER = "TIFF16"
# Mode 5: process only TIFFs in this subfolder of _EXPORT.
# Prevents accidentally converting AdobeRGB, sRGB, or WEB exports.
# Set to "" to process all subfolders inside _EXPORT.

ENCODE_TAG_MODE = "software"
# "software" → appends "| cjxl d=X e=Y" to the EXIF Software field
# "xmp"      → writes as XMP-dc:Description
# "off"      → no encoding tag

OVERWRITE = False
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
```

---

## Output modes

| Mode | Output location | Example |
|------|----------------|---------|
| `0` | Flat: input → output folder | `output/photo.jxl` |
| `1` | Creates `converted_jxl/` inside each TIFF folder | `.../TIFF/converted_jxl/photo.jxl` |
| `2` | Creates `JXL_16bits/` next to each TIFF folder | `.../JXL_16bits/photo.jxl` |
| `3` | Renames folder replacing `TIFF` → `JXL` | `.../Export_JXL/photo.jxl` |
| `4` | `_EXPORT` anchor — all TIFFs in hierarchy | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `5` ⭐ | `_EXPORT` anchor — only TIFFs inside `_EXPORT` | `.../session/_EXPORT/16B_JXL/photo.jxl` |

**Mode 5 example** with `EXPORT_TIFF_SUBFOLDER = "TIFF16"`:
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
  --mode 0-5      Output folder mode (default: 0)
  --workers N     Parallel threads (tested up to 32 on a Ryzen 9 5950X)
  --overwrite     Always overwrite existing JXLs
  --sync          Reconvert only TIFFs newer than their JXL
```

---

## Known behavior — IrfanView and color-calibrated monitors

This is not a bug in the script, but it is worth documenting because it produces confusing results.

JXL lossless files embed the ICC color profile as a blob. Most software handles this correctly — GIMP, XnView MP, Darktable, Firefox, Waterfox, and jxl_to_jpeg.py all display correct colors.

IrfanView's behavior with lossless JXL appears to depend on the system display profile installed on the machine. In my testing, it worked correctly on an uncalibrated monitor. After hardware calibration with an Eizo monitor, IrfanView stopped showing correct colors for lossless JXL while continuing to show correct colors for lossy JXL. The cause appears to be double color management — IrfanView applying both the embedded ICC and the system display profile simultaneously.

Lossy JXL (d > 0) uses native JXL color primaries instead of an ICC blob and is not affected by this issue.

The files themselves are correct. Any conformant JXL decoder will display the colors accurately. The issue is specific to IrfanView on calibrated systems.

If lossless JXL colors look wrong in IrfanView, use lossy at d=0.1 (imperceptible difference, ~34MB for 45MP), or open the files in any of the viewers listed above. Files also convert correctly to JPEG using jxl_to_jpeg.py.

---

## Performance

With `USE_RAM_FOR_PNG = True` (default), disk I/O per file = read TIFF + write JXL.
The PNG intermediate (~200MB) lives entirely in RAM.

With `TEMP2_DIR` set to a separate SSD, JXLs are written to fast storage during conversion
and moved in bulk at the end — eliminates random write contention on HDD collections.

---

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
