# tiff_to_jxl.py

Batch TIFF 16-bit → JPEG XL converter. Encodes TIFF files to JXL format with 
configurable quality (lossless or lossy), preserves full EXIF/XMP metadata, 
and **embeds the original ICC color profile as XMP metadata** for perfect 
round-trip preservation.

Works with any 16-bit TIFF — Capture One exports, NX Studio, Photoshop, or 
standard uncompressed TIFFs from various sources.

**Key feature:** ICC profile embedding. 
```
When paired with `jxl_to_tiff.py`, the exact original ICC profile is 
preserved even for **lossy JXL files**, which would otherwise lose 
the detailed ICC information (gamma curves, copyright, etc.).
```

---

## Requirements

```
Python 3.12+
pip install tifffile numpy pillow
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
# [Mode 6/8 only] Whether to delete the source TIFF after successful encode.
# WARNING: irreversible. Only enable after testing on a small batch first.

# — Safety (mode 6 + DELETE_SOURCE only) —
DELETE_CONFIRM = True
# True  → require interactive confirmation before deleting source files
# False → skip confirmation (for automation only)
```

#### Safety confirmation (mode 6 + DELETE_SOURCE)
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
| `2` | Directory | Flat → output_dir | `output_dir/photo.jxl` |
| `3` | Directory | `converted_jxl/` inside each TIFF folder | `.../TIFF/converted_jxl/photo.jxl` |
| `4` | Directory | Rename folder `TIFF` → `JXL` | `.../Export_JXL/photo.jxl` |
| `5` | Directory | Sibling folder `JXL_16bits/` | `.../JXL_16bits/photo.jxl` |
| `6` | Directory | `_EXPORT` anchor — all TIFFs in hierarchy | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `7` | Directory | `_EXPORT` anchor — only TIFFs inside `_EXPORT` | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `8` | Directory | In-place recursive — JXL next to each TIFF | `.../session/photo.jxl` |

---

## CLI reference

```
py tiff_to_jxl.py <input> [output] [options]

Arguments:
  input           Input root folder or file
  output          Output folder (mode 0 only)

Options:
  --mode 0-8      Output folder mode (default: 0)
  --workers N     Parallel threads (tested up to 32 on a Ryzen 9 5950X)
  --overwrite     Always overwrite existing JXLs
  --sync          Reconvert only TIFFs newer than their JXL
```

---

## ICC Profile Preservation

### Why This Matters

When converting TIFF → **lossy JXL** (`d>0`), the JXL encoder:
1. Converts the ICC color profile to **native primaries** (for efficient encoding)
2. The original ICC detail (TRC curves, copyright, etc.) is **not stored** in the JXL codestream

Without ICC embedding:
```
TIFF (ProPhoto ICC) → JXL (lossy, primaries only) → TIFF (generic ICC from primaries)
```

With ICC embedding (`EMBED_ICC_IN_JXL = True`, default):
```
TIFF (ProPhoto ICC) → JXL (lossy + XMP with base64 ICC) → TIFF (original ProPhoto ICC restored)
```

### How It Works

1. **Extract ICC** from source TIFF using exiftool
2. **Base64-encode** the ICC profile
3. **Embed in XMP** metadata (`dc:Description` field with `ICC:` prefix)
4. **Encoding params** (cjxl d=0.1 e=7) go to `XMP:CreatorTool` (visible in Windows)

When converting back with `jxl_to_tiff.py`:
1. Extract ICC from XMP metadata
2. Apply to output TIFF
3. Clean up XMP (remove base64 data, keep encoding params)

### Technical Details

The embedded ICC is stored as:
```xml
<dc:description>
  <rdf:Alt>
    <rdf:li xml:lang="x-default">ICC:AAADrEtDTVMCEAAAbW50clJHQiBYWVogB84A...</rdf:li>
  </rdf:Alt>
</dc:description>
<xmp:CreatorTool>cjxl d=0.1 e=7 | TIFF to JXL with ICC preservation</xmp:CreatorTool>
```

The base64 string is not human-readable but preserves the **exact binary ICC data**.

→ See [JXL Color Internals](jxl_color_internals.md) for more technical details.

---

## Relationship with jxl_to_tiff.py

These scripts are designed to work as a pair:

```powershell
# Encode: TIFF → JXL (with ICC embedding)
py tiff_to_jxl.py "photo.tif" --mode 0

# Decode: JXL → TIFF (ICC restored)
py jxl_to_tiff.py "photo.jxl" --mode 0
```

**For best results:**
- Use `EMBED_ICC_IN_JXL = True` (default) in `tiff_to_jxl.py`
- Both scripts detect and handle the embedded ICC automatically

---

## Performance

With `USE_RAM_FOR_PNG = True` (default), the PNG intermediate (~200MB) lives entirely 
in RAM. Disk I/O per file = read TIFF + write JXL.

With `TEMP2_DIR` set to a separate SSD, JXLs are written to fast storage during conversion
and moved in bulk at the end — eliminates random write contention on HDD collections.

---

## Logs

```
<script_folder>/Logs/tiff_to_jxl/YYYYMMDD_HHMMSS.log
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
- [Kimi](https://www.kimi.com) (Moonshot AI) and Claude (Anthropic) for code assistance and technical discussion
