# jxl_tiff_encoder.py

Batch TIFF 16-bit → JPEG XL converter. Encodes TIFF files to JXL format with 
configurable quality (lossless or lossy), preserves full EXIF/XMP metadata, 
and **embeds the original ICC color profile as XMP metadata** for perfect 
round-trip preservation.

Works with any 16-bit TIFF — Capture One exports, NX Studio, Photoshop, or 
standard uncompressed TIFFs from various sources.

**Key feature:** ICC profile embedding. 
```
When paired with `jxl_tiff_decoder.py`, the exact original ICC profile is 
preserved even for **lossy JXL files**, which would otherwise lose 
the detailed ICC information (gamma curves, copyright, etc.).
```

---

## Requirements

```
Python 3.12+
pip install tifffile numpy
cjxl  →  https://github.com/libjxl/libjxl/releases
exiftool  →  https://exiftool.org
```

Both `cjxl.exe` and `exiftool.exe` must be on your PATH.

### Download the Correct Files

| Tool | Download | What to Get |
|------|----------|-------------|
| **cjxl** | https://github.com/libjxl/libjxl/releases | `jxl-x64-windows-static.zip` ⚠️ **(NOT `jxl-x64-windows.zip` which has only DLLs)** |
| **exiftool** | https://exiftool.org | `exiftool-XX.XX_64.zip` ⚠️ **(Windows .zip, NOT .tar.gz source)** |

### exiftool Setup (Important!)

The download comes as `exiftool(-k).exe`. **Rename it:**

```powershell
# Option A: Rename
Rename-Item "C:\tools\exiftool\exiftool(-k).exe" "exiftool.exe"

# Option B: Duplicate and rename (keeps original)
Copy-Item "C:\tools\exiftool\exiftool(-k).exe" "C:\tools\exiftool\exiftool.exe"
```

### Add to PATH

**Replace with YOUR actual paths:**

```powershell
$myPaths = @(
    "C:\tools\libjxl\bin",    # where cjxl.exe is
    "C:\tools\exiftool"        # where exiftool.exe is (RENAMED!)
)
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", ($myPaths -join ";") + ";$p", "User")
# Restart PowerShell after this!
```

### Verify
```powershell
cjxl --version      # JPEG XL encoder v0.11.x
exiftool -ver       # 13.xx
```

---

## Quick start

```powershell
# ── The easy way — mode 0, no flags needed ──────────────────────
# Single file, in-place
py jxl_tiff_encoder.py "F:\Photos\photo.tif"

# Single file → specific output folder
py jxl_tiff_encoder.py "F:\Photos\photo.tif" "F:\output"

# Whole folder, in-place (flat — subfolders not touched)
py jxl_tiff_encoder.py "F:\Photos"

# Whole folder → specific output folder (flat)
py jxl_tiff_encoder.py "F:\Photos" "F:\output"

# ── Other modes ──────────────────────────────────────────────────
# Capture One _EXPORT workflow (mode 7) — most common for C1 users
py jxl_tiff_encoder.py "F:\2024" --mode 7

# Sync — only reconvert TIFFs newer than existing JXL
py jxl_tiff_encoder.py "F:\2024" --mode 7 --sync

# 16 parallel workers
py jxl_tiff_encoder.py "F:\2024" --mode 7 --workers 16

# Mode 8 — in-place recursive: JXL next to each TIFF, all subfolders
py jxl_tiff_encoder.py "F:\2024" --mode 8
```

---

## Key settings

Edit at the top of the script:

```python
CJXL_DISTANCE = 0.1
# 0   = mathematically lossless (pixel-perfect, ~173MB for 45MP)
# 0.05 = near-lossless (~47MB, imperceptible difference) ⭐ RECOMMENDED for archive
# 0.1 = near-lossless (~34MB, imperceptible difference)
# 0.5 = high quality lossy (~13MB) — recommended starting point (libjxl authors)
# 1.0 = "visually lossless" per libjxl documentation (~8MB)

CJXL_EFFORT = 7
# Compression effort (1-10). Controls file size, NOT quality.
# 7 is the sweet spot for camera photos.
# Effort 8-10 is much slower and can produce larger files for high-ISO images.

EMBED_ICC_IN_JXL = True
# Embeds the original ICC profile as metadata in the JXL file.
# The ICC is NOT used by the JXL decoder (JXL uses native primaries),
# but is preserved for round-trip conversion back to TIFF/JPEG.
# This ensures the exact original ICC (with TRC curves, copyright, etc.)
# is available when converting JXL → TIFF, even for lossy JXLs.
# True  → embed ICC profile in JXL metadata (recommended, default)
# False → do not embed ICC (smaller file, but lossy JXLs will use generic ICC on decode)

ENCODE_TAG_MODE = "xmp"
# Records encoding parameters in the JXL metadata.
# "software" → appends to the EXIF Software field
# "xmp"      → writes as XMP metadata (default)
# "off"      → does not add anything
# Can also be set via --encode-tag CLI argument (xmp/software/off)
# NOTE: When EMBED_ICC_IN_JXL is True, encoding params go to XMP:CreatorTool
# (dc:Description is used for ICC embedding).

D50_PATCH_MODE = "auto"
# D50 illuminant patch for Capture One ICC compatibility.
# Capture One has a bug where the D50 illuminant values are slightly off
# (rounding error). This patch fixes them for cjxl compatibility.
# "on"   → Always apply the patch
# "off"  → Never apply the patch (use original ICC values)
# "auto" → Only apply if source software matches D50_PATCH_SOFTWARE_LIST
# Can also be set via --d50-patch CLI argument (on/off/auto)

D50_PATCH_SOFTWARE_LIST = ["capture one", "captureone"]
# Software names that trigger D50 patch when D50_PATCH_MODE="auto".
# Case-insensitive matching. Add your own software here if it has the same ICC bug.
# The list is checked against EXIF Software field.

CLEANUP_XMP_ICC_MARKER = False
# Remove legacy ICC markers from XMP if present.
# True  → clears xmp-icc:all and xmp-photoshop:ICCProfile tags that might conflict
# False → keeps existing ICC markers (default)

USE_RAM_FOR_PNG = False
# True  → PNG intermediate stays entirely in RAM (faster, ~400MB RAM per worker)
# False → PNG is written to disk in TEMP_DIR (useful if RAM is limited)

TEMP2_DIR = r"E:\staging"
# Staging SSD for output JXLs. Separates read I/O (HDD with TIFFs) from write I/O.
# Files are moved to their final destination after each folder group completes.
# Set to None to write directly to the final destination.
# Can also be set via --staging CLI argument (overrides this variable).

OVERWRITE = "smart"
# False   → skip if JXL already exists (safe for resuming)
# True    → always overwrite
# "smart" → same as --sync: reconvert only if TIFF is newer than JXL

DELETE_SOURCE = False
# [Mode 8 only] Whether to delete the source TIFF after successful encode.
# WARNING: irreversible. Only enable after testing on a small batch first.

# — Safety (mode 8 + DELETE_SOURCE only) —
DELETE_CONFIRM = True
# True  → require interactive confirmation before deleting source files
# False → skip confirmation (for automation only)
```

#### Safety confirmation (mode 8 + DELETE_SOURCE)
```
When `DELETE_SOURCE = True` and `DELETE_CONFIRM = True`:
- **Lossless:** type `yes` to confirm
- **Lossy:** type the current time in `HHMM` format (forces conscious decision)
```



---

## Modes 6 and 7 — ONLY files inside `_EXPORT`

**Modes 6 and 7 ONLY process files inside folders containing `_EXPORT`. Everything outside is IGNORED.**

```
E:\sessao\
├── foto1.tif          ← NOT processed (outside _EXPORT)
├── foto2.tif          ← NOT processed (outside _EXPORT)
└── _EXPORT\
    ├── folder1\
    │   └── img.tif    ← PROCESSED ✓
    ├── folder2\
    │   └── img.tif    ← PROCESSED ✓
    └── folder3\sub\
        └── img.tif    ← PROCESSED ✓
```

**Mode 6** — processes ALL TIFFs under ALL `_EXPORT` folders.

**Mode 7** — only TIFFs inside a specific subfolder of `_EXPORT` (default: `_EXPORT/16B_TIFF` → output to `_EXPORT/16B_JXL`).

```
Mode 7 example with default settings:
session/_EXPORT/16B_TIFF/photo.tif → session/_EXPORT/16B_JXL/photo.jxl  ✓
session/_EXPORT/AdobeRGB/photo.tif → ignored
```

---

## Output modes

| Mode | Input | Output location | Example |
|------|-------|----------------|---------|
| `0` | File: single file / Folder: all files | In-place (output in same folder) | `photo.jxl` |
| `1` | Single file | `converted_jxl/` subfolder next to source | `.../converted_jxl/photo.jxl` |
| `2` | Directory | Flat → output_dir (recursive) | `output_dir/photo.jxl` |
| `3` | Directory | `converted_jxl/` inside each TIFF folder | `.../TIFF/converted_jxl/photo.jxl` |
| `4` | Directory | Rename folder `TIFF` → `JXL` | `.../Export_JXL/photo.jxl` |
| `5` | Directory | Sibling folder `JXL_16bits/` | `.../JXL_16bits/photo.jxl` |
| `6` | Directory | ONLY TIFFs INSIDE `_EXPORT` — ignores everything outside | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `7` | Directory | Like mode 6 but only specific `_EXPORT` subfolder | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `8` | Directory | In-place recursive — JXL next to each TIFF | `.../session/photo.jxl` |

---

## CLI reference

```
py jxl_tiff_encoder.py <input> [output] [options]

Arguments:
  input           Input root folder or file
  output          Output folder (mode 0 only)

Options:
  --mode 0-8      Output folder mode (default: 0)
  --workers N     Parallel threads (tested up to 32 on a Ryzen 9 5950X)
  --overwrite     Always overwrite existing JXLs
  --sync          Reconvert only TIFFs newer than their JXL
  --distance N    JXL distance (0=lossless, 0.1=near-lossless, default: from script)
  --effort 1-10  Compression effort (default: from script setting)
  --ram           Keep PNG intermediate in RAM (faster, more memory)
  --no-ram        Write PNG intermediate to disk (slower, less memory)
  --delete-source Delete source TIFFs after successful encode (mode 8 only)
  --staging DIR   Staging directory for output JXLs (reduces HDD seek contention)
  --encode-tag    Where to record encoding params: xmp (default), software, off
  --d50-patch     D50 illuminant patch: on (always), off (never), auto (detect)
  --dry-run       Preview operations without converting
```

**D50 Patch option:**
```powershell
# Force D50 patch on all files
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --d50-patch on

# Disable D50 patch entirely
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --d50-patch off

# Auto-detect based on EXIF Software (default behavior)
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --d50-patch auto
```

---

## ICC Profile Preservation

### Why This Matters

When converting TIFF → **lossy JXL** (`d>0`), the `cjxl` encoder by default:
1. Converts the ICC color profile to **native primaries** (for efficient encoding)
2. The original ICC detail (TRC curves, copyright, etc.) is **discarded** in favor of compact primaries

The JXL format itself supports full ICC in lossy mode, but the reference encoder 
optimizes for size unless explicitly configured otherwise.

#### Without ICC embedding:
```
TIFF (ProPhoto ICC) → JXL (lossy, primaries only) → TIFF (generic ICC from primaries)
```

#### With ICC embedding (`EMBED_ICC_IN_JXL = True`, default):
```
TIFF (ProPhoto ICC) → JXL (lossy + XMP with base64 ICC) → TIFF (original ProPhoto ICC restored)
```
**OBS: If all you need is small file sizes, disable this funcition: it leads to bigger files.**

---

### How It Works

1. **Extract ICC** from source TIFF using exiftool (original ICC for XMP, patched for PNG)
2. **Base64-encode** the ICC profile
3. **Embed in XMP** metadata (`xmp:CreatorTool` field with `ICC:` prefix)
4. **Encoding params** (cjxl d=0.1 e=7) go to `dc:description` (visible in Windows Properties)

When converting back with `jxl_tiff_decoder.py`:
1. Extract ICC from XMP metadata
2. Apply to output TIFF
3. Clean up XMP (remove base64 data, keep encoding params)

---

### Technical Details

The embedded ICC is stored as:
```xml
<xmp:CreatorTool>ICC:AAADrEtDTVMCEAAAbW50clJHQiBYWVogB84A...</xmp:CreatorTool>
<dc:description>
  <rdf:Alt>
    <rdf:li xml:lang="x-default">cjxl d=0.1 e=7</rdf:li>
  </rdf:Alt>
</dc:description>
```

The base64 string is not human-readable but preserves the **exact binary ICC data**.

---

### Important: ICC Extraction Strategy

The script uses **two separate ICC extractions**:

1. **ICC for PNG (cjxl encoding)**: Patched D50 illuminant for Capture One compatibility
2. **ICC for XMP (preservation)**: Original unmodified ICC for perfect round-trip

This ensures:
- cjxl receives a compatible ICC for encoding
- The exact original ICC is preserved for restoration

→ See [JXL Color Internals](jxl_color_internals.md) for more technical details.

---

## D50 Illuminant Patch

### What is it?

Capture One (and some other software) **may create** ICC profiles with a **slightly incorrect D50 illuminant value** due to a rounding error. The ICC specification defines D50 as:
- X = 0.9642, Y = 1.0000, Z = 0.8249

But Capture One writes:
- X = 0.964202, Y = 1.000000, Z = 0.824905

This tiny difference causes `cjxl` to fail or produce warnings when encoding with the ICC profile.

### How the patch works

The script automatically **corrects the D50 illuminant bytes** in the ICC profile before passing it to cjxl:

```
Original ICC → Patch D50 bytes → cjxl encoding
      ↓                              ↓
  XMP storage                    JXL file
```

### Configuration

**Three modes available:**

| Mode | Behavior |
|------|----------|
| `on` | Always apply D50 patch (forces correction on all files) |
| `off` | Never apply D50 patch (use original ICC values) |
| `auto` | Only apply if EXIF Software matches known buggy software (default) |

**Default detection list:**
```python
D50_PATCH_SOFTWARE_LIST = [
    "capture one",
    "captureone",
    # "my software",  # <-- add more software names here (uncomment to enable)
]
```

You can customize this list in the script settings to add other software with the same bug.

### Summary output

After conversion completes, the script shows D50 patch statistics:

```
Done: 42 OK | 0 overwrites | 0 skipped | 0 errors
D50 patch: 15 applied | 27 skipped (mode: auto)
```

This helps you verify that the auto-detection is working correctly for your files.

---

## Relationship with jxl_tiff_decoder.py

These scripts are designed to work as a pair:

```powershell
# Encode: TIFF → JXL (with ICC embedding)
py jxl_tiff_encoder.py "photo.tif" --mode 0

# Decode: JXL → TIFF (ICC restored)
py jxl_tiff_decoder.py "photo.jxl" --mode 0
```

**For best results:**
- Use `EMBED_ICC_IN_JXL = True` (default) in `jxl_tiff_encoder.py`
- Both scripts detect and handle the embedded ICC automatically

---

## XMP Preservation (Fixed in this version)

### The XMP Overwrite Bug (Fixed)

Previous versions had a bug where XMP metadata was overwritten:
1. First, EXIF/XMP was copied from TIFF
2. Then, a second pass overwrote ALL XMP with just the ICC data

**Result**: Original ratings, keywords, and descriptions were lost!

### The Fix

This version uses **targeted XMP updates**:
- `-xmp-dc:Description=` for encoding params (concatenated with existing dc:description)
- `-xmp-xmp:CreatorTool=` for ICC data (base64-encoded ICC profile)
- All other XMP tags preserved via `-tagsfromfile`

**Result**: Original metadata + encoding info + ICC all coexist!

---

## Performance

With `USE_RAM_FOR_PNG = True` (default), the PNG intermediate (~200MB) lives entirely 
in RAM. Disk I/O per file = read TIFF + write JXL.

With `--staging` (or `TEMP2_DIR`) set to a separate SSD, JXLs are written to fast storage during conversion
and moved in bulk at the end — eliminates random write contention on HDD collections.

---

## Logs

```
<script_folder>/Logs/jxl_tiff_encoder/YYYYMMDD_HHMMSS.log
```

Opening line shows all active settings:
```
Mode: 7 | Effort: 7 | Distance: 0.1 | RAM PNG: False | D50: auto | Workers: 16
```

Final summary includes D50 patch statistics (when applicable):
```
Done: 42 OK | 0 overwrites | 0 skipped | 0 errors
D50 patch: 15 applied | 27 skipped (mode: auto)
```


---

## How to verify output

```powershell
# Check JXL has embedded ICC
exiftool -XMP-dc:Description photo.jxl | findstr "ICC:"

# Check encoding params
exiftool -XMP-xmp:CreatorTool photo.jxl

# Check EXIF Software (for D50 patch detection)
exiftool -Software photo.jxl

# Full JXL info
jxlinfo -v photo.jxl
```


---

## Known behaviors 

### IrfanView and color-calibrated monitors
```
JXL lossless files embed the ICC color profile as a blob. Most software handles this
correctly — GIMP, XnView MP, Darktable, Firefox, Waterfox, and `jxl_to_jpeg.py` all
display correct colors.

IrfanView's behavior with lossless JXL appears to depend on the system display profile
installed on the machine. The issue is specific to IrfanView on calibrated systems.

**The files themselves are correct.** Any conformant JXL decoder will display the colors
accurately. If lossless JXL colors look wrong in IrfanView, use lossy at `d=0.1` 
(imperceptible difference), or open the files in any of the other viewers listed above.

*For detailed technical information about JXL color management, XYB vs non-XYB, 
ICC blobs vs native primaries, and primary coordinates reference tables, 
see [JXL Color Internals](jxl_color_internals.md).*
```

### XnView MP color profile display for lossy JXL
```
XnView MP shows `Color Profile: sRGB` in the properties panel for lossy JXL files,
even when the actual colorspace is ProPhoto RGB or AdobeRGB.

**This is not a conversion to sRGB.** It is a display error in XnView MP's metadata panel.

Lossy JXL encodes colorspace information as compact numeric primaries (CICP-style),
not as an embedded ICC blob. XnView reads the ICC blob field, finds nothing, and
falls back to showing "sRGB" as a default label.

The actual colorspace is correctly preserved and correctly rendered — as confirmed
by `jxlinfo` and by every other viewer (GIMP, Darktable, browsers, etc.).

→ See [JXL Color Internals](jxl_color_internals.md) for full details.
```
---

## Disclaimer

These tools were made for my personal workflow. 
Use at your own risk — I am not responsible for any issues you may encounter.

However, If you find any bugs, feel free to report to me - I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.

---

## Changes since v1.0

### New Features

**D50 illuminant patch — auto-detection (default)**
Capture One **may export** files with a known ICC rounding error that causes cjxl warnings. The patch was already part of the toolkit, but now supports three operating modes:

- `auto` (default): Only applies D50 patch when EXIF `Software` field contains `capture one` or `captureone` — other files are unaffected.
- `on`: Always applies the D50 patch to all files (forces correction regardless of source software).
- `off`: Never applies the D50 patch (uses original ICC values as-is).

CLI flag: `--d50-patch auto|on|off`
Script setting: `D50_PATCH_MODE = "auto"` (default)

### Bug Fixes
- Integer overflow in JXL box parser (size validation added)
- Race condition in staging directory (UUID-based filenames)
- D50 patch statistics now shown in summary output

Full tracking: [bug_tracking_since_v1.0.md](./bug_tracking_since_v1.0.md)

---

## License

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation  
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling  
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O  
- [MiniMax](https://www.minimax.io/) (MiniMax AI) and [Kimi](https://www.kimi.com) (Moonshot AI) for code assistance and technical discussion
