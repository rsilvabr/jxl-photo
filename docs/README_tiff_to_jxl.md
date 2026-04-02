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
# NOTE: When EMBED_ICC_IN_JXL is True, encoding params go to XMP:CreatorTool
# (dc:Description is used for ICC embedding).

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

## Output modes

| Mode | Input | Output location | Example |
|------|-------|----------------|---------|
| `0` | File or folder | In-place or → output_dir (flat, non-recursive) | `photo.jxl` / `output_dir/photo.jxl` |
| `1` | Single file | `converted_jxl/` subfolder next to source | `.../converted_jxl/photo.jxl` |
| `2` | Directory | Flat → output_dir (recursive) | `output_dir/photo.jxl` |
| `3` | Directory | `converted_jxl/` inside each TIFF folder | `.../TIFF/converted_jxl/photo.jxl` |
| `4` | Directory | Rename folder `TIFF` → `JXL` | `.../Export_JXL/photo.jxl` |
| `5` | Directory | Sibling folder `JXL_16bits/` | `.../JXL_16bits/photo.jxl` |
| `6` | Directory | `_EXPORT` anchor — all TIFFs in hierarchy | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `7` | Directory | `_EXPORT` anchor — only TIFFs inside `_EXPORT` | `.../session/_EXPORT/16B_JXL/photo.jxl` |
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
  --dry-run       Preview operations without converting
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

With `TEMP2_DIR` set to a separate SSD, JXLs are written to fast storage during conversion
and moved in bulk at the end — eliminates random write contention on HDD collections.

---

## Logs

```
<script_folder>/Logs/jxl_tiff_encoder/YYYYMMDD_HHMMSS.log
```

Opening line shows all active settings including `EMBED_ICC_IN_JXL` status.


---

## How to verify output

```powershell
# Check JXL has embedded ICC
exiftool -XMP-dc:Description photo.jxl | findstr "ICC:"

# Check encoding params
exiftool -XMP-xmp:CreatorTool photo.jxl

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

However, If you find any bugs, fell free to report to me - I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.
---

## License

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation  
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling  
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O  
- [MiniMax](https://www.minimax.io/) (MiniMax AI) and [Kimi](https://www.kimi.com) (Moonshot AI) for code assistance and technical discussion
