# JXL-TIFF-JPEG-PNG Converter

JPEG XL conversion tools with **full ICC color profile preservation** and full EXIF metadata correctly embedded and visible in IrfanView, XnView MP, and other applications. Designed for enthusiasts and photographers working with 16-bit TIFF files who desire to compact their photos keeping 16-bit tonal range. Tested with Capture One, Lightroom, NX Studio, Photoshop and Fuji Hyper Utility exported 16-bit TIFFs.

---

# Why JPEG XL?

Spectacular compression with no compromise on bit depth.

- Lossless 16-bit files much smaller than TIFF and TIFF with ZIP/Deflate
- Lossy 16-bit files — small files that retain full 16-bit tonal information, something no other common format achieves (JPEG is 8-bit, TIFF lossless is large)
- This is genuinely new: small lossy files, but with 16-bit color depth

Here is an example of the gains when using JXL with 45MP Nikon Z7 files:

| Format | Typical size (45MP, 16-bit) |
|--------|-----------------------------|
| TIFF 16-bit | ~260 MB, ~245 MB (zip/deflate) |
| JXL 16-bit lossless | ~173 MB |
| JXL 16-bit lossy `d=0.05` | ~47 MB |
| JXL 16-bit lossy `d=0.1` | ~34 MB |
| JXL 16-bit lossy `d=1.0` (visually lossless) | ~8 MB |

I have tested with different settings and posted on reddit, [click here to check](https://www.reddit.com/r/jpegxl/comments/1s6k718/edit_stress_test_lossy_jxl_under_heavy_editing/). 

# Features

### 1. **ICC Profile Preservation** 

Professional color management requires precise ICC profiles with full TRC (Tone Reproduction Curve) data, not just generic color primaries. While the JPEG XL format fully supports ICC profiles in both lossless and lossy modes, the reference encoder (`cjxl`) optimizes file size by converting detailed ICC profiles to compact "native primaries" representation when using lossy compression.

This toolkit ensures **bit-exact ICC preservation** across all conversion paths:

- **Lossless mode:** Leverages native JXL ICC container support (standard behavior)
- **Lossy mode:** Embeds original ICC as base64-encoded XMP metadata before encoding, then restores it on decode — bypassing the encoder's optimization that would otherwise discard TRC curves and copyright metadata
- **Round-trip safety:** TIFF → JXL → TIFF maintains identical ICC data, gamma curves, and device calibration metadata

**Why this matters:**  
Native primaries are sufficient for display, but professionally calibrated workflows rely on precise TRC curves found only in full ICC profiles. This toolkit guarantees your color profiles survive compression intact, regardless of encoder optimization settings.

### 2. **Three Decode Modes for JXL → TIFF**

**Roundtrip Mode** (default when ICC present): Uses `djxl auto` + original ICC attachment. Best for files from `tiff_to_jxl.py`. Visually perfect and fast.

**Basic Mode** (default when no ICC): Uses `djxl auto` only. For consumer JXLs without embedded profiles.

**Matrix Mode** (optional `--matrix`): Linear Rec.2020 decode + LittleCMS transform. For color space conversion or special workflows.

### 3. **JPEG Preview Embedding**
TIFF output includes a second page with JPEG-compressed preview for fast 
thumbnail generation in Windows Explorer and other file managers.

### 4. **Professional Workflow Support**
- 16-bit TIFF preservation
- Full EXIF/XMP metadata preservation (fixed in this version!)
- Multiple folder structure modes (flat, recursive, Capture One or Lightroom EXPORT)
- Parallel processing (tested up to 32 workers)
- Sync mode (only reconvert changed files)

---

##  What's New in This Version

### Decode Modes 
- **Roundtrip** : `djxl auto` + original ICC — recommended for most use
- **Basic** : `djxl auto` only — for web/mobile JXLs
- **Matrix** : Linear + LittleCMS — for color space conversion



### Bug Fixes

#### XMP Preservation Fixed
Previous versions had a critical bug where XMP metadata was **overwritten** instead of preserved:
- **Old behavior**: First copy EXIF/XMP, then overwrite all XMP with ICC data
- **Result**: Ratings, keywords, and original descriptions were lost!
- **New behavior**: Targeted XMP updates using `-xmp-dc:Description=` and `-xmp-xmp:CreatorTool=`
- **Result**: Original metadata + encoding info + ICC all coexist

#### EXIF Extraction Fixed
Previous versions used binary EXIF extraction which could corrupt data:
- **Old method**: `exiftool -b -Exif -o exif.bin` then inject
- **Problem**: Produced 27MB corrupted files instead of 880 bytes
- **New method**: `exiftool -tagsfromfile source -exif:all destination`
- **Result**: EXIF now correctly visible in IrfanView

#### ICC Handling Improved
- Separate ICC extraction: **patched for PNG encoding** (cjxl compatibility) vs **original for preservation** (perfect round-trip)
- ICC from XMP now used with **Roundtrip Mode** (djxl auto + direct ICC apply) avoiding double gamma issues

#### ImageDescription Cleanup
Fixed `tifffile` adding `{"shape": [H,W,C]}` to ImageDescription (both IFD0 and IFD1/preview). Now cleaned automatically.

---

##  Scripts

| Script | Purpose | Key Feature |
|--------|---------|-------------|
| [`tiff_to_jxl.py`](tiff_to_jxl.py) | TIFF → JXL encoder | Embeds ICC in XMP for round-trip preservation, concatenates description |
| [`jxl_to_tiff.py`](jxl_to_tiff.py) | JXL → TIFF decoder | Restores original ICC from XMP using Roundtrip Mode (no double gamma), adds JPEG preview |
| [`jxl_to_jpg_png.py`](jxl_to_jpg_png.py) | JXL → JPG/PNG encoder | Batch JXL → JPEG/PNG with ICC profile conversion |


---

##  Quick Start

### Typical workflow


```
Capture One
    ↓ Export 16-bit TIFF (sRGB, AdobeRGB, ProPhoto RGB)
tiff_to_jxl.py      TIFF → JXL  (archive, stays 16-bit, lossless or lossy)
    ↓
    JXLs on disk — ~8–47MB each for lossy, ~173MB for lossless (45MP example)
    ↓
jxl_to_jpeg_png.py      JXL → JPEG/PNG  (when needed for print or delivery)
                                  ICC profile conversion applied here

(With jxl_to_tiff used when TIFF is needed again.)
```

### TIFF → JXL

```powershell
# Single file
py tiff_to_jxl.py "photo.tif"

# Folder (Capture One _EXPORT workflow)
py tiff_to_jxl.py "F:\Photos\2024" --mode 7

# With settings
py tiff_to_jxl.py "photo.tif" --mode 0 --workers 8
```

### JXL → TIFF

```powershell
# Single file — auto mode (Roundtrip if has ICC, Basic if not)
py jxl_to_tiff.py "photo.jxl"

# Force Matrix mode for color space conversion
py jxl_to_tiff.py "photo.jxl" --matrix --target-icc "C:\icc\sRGB.icc"

# Folder
py jxl_to_tiff.py "F:\Photos\2024" --mode 7

# 8-bit output for web
py jxl_to_tiff.py "photo.jxl" --depth 8
```

### JXL → JPG/PNG

```powershell
# Convert JXL files with custom profile to sRGB JPEG in sibling folder
py jxl_to_jpeg_png.py "F:\2024\session\_EXPORT\16B_JXL"

# Convert to a Custom ICC profile (AdobeRGB, etc.)
py jxl_to_jpeg_png.py "F:\2024" --icc-profile "C:\icc\AdobeRGB1998.icc"

# 16-bit PNG for print workflows
py jxl_to_jpeg_png.py "F:\2024" --format png --bit-depth 16
```


### After conversion
Depending on your needs, two common approaches:

1. Keep both TIFF and JXL — exclude the TIFF export folders from backups to save space. Tools like FreeFileSync support folder filters that make this easy.
2. Delete TIFFs, keep only JXL — a separate script for this can be found here: [delete-tiff-exports](https://github.com/rsilvabr/delete-tiff-exports)
3. Use the configurable option to delete TIFFs after conversion available in this script. 



---

##  Documentation

| Document | Contents |
|----------|----------|
| [docs/README_tiff_to_jxl.md](docs/README_tiff_to_jxl.md) | Full documentation for TIFF → JXL conversion |
| [docs/README_jxl_to_tiff.md](docs/README_jxl_to_tiff.md) | Full documentation for JXL → TIFF conversion |
| [docs/README_jxl_to_jpg_png.md](docs/README_jxl_to_jpg.md) | Full documentation for JXL → JPG/PNG conversion |
| [docs/jxl_color_internals.md](docs/jxl_color_internals.md) | Deep dive: XYB, ICC blobs vs primaries, troubleshooting |

---

## Requirements

```
Python 3.12+
pip install tifffile numpy pillow
cjxl / djxl → https://github.com/libjxl/libjxl/releases
exiftool → https://exiftool.org
```

Quick setup (PowerShell):
```powershell
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", "$p;C:\tools\libjxl\bin;C:\tools\exiftool", "User")
```

---

##  ICC Preservation: How It Works

### Without This Toolkit (default cjxl behavior)

```
TIFF (ProPhoto ICC with Kodak TRC curves)
    ↓ cjxl lossy (default settings)
JXL (native primaries + minimal ICC - TRC detail optimized away)
    ↓ djxl
TIFF (generic ICC generated from primaries - sufficient for display only)
```

**Problem:** Generic ICC works for viewing, but lacks:
- Precise tone reproduction curves (TRC)
- Copyright and manufacturer metadata
- Device-specific calibration

### With This Toolkit

```
TIFF (ProPhoto ICC with Kodak TRC curves)
    ↓ tiff_to_jxl.py (EMBED_ICC_IN_JXL = True)
JXL (native primaries + XMP with base64 ICC)
    ↓ jxl_to_tiff.py (Roundtrip Mode)
TIFF (original ProPhoto ICC restored!)
```

**Result:** Exact original ICC with all metadata intact.

### Technical Details

The ICC is base64-encoded and stored in XMP:

```xml
<dc:description>ICC:AAADrEtDTVMCEAAAbW50clJHQiBYWVog...</dc:description>
<xmp:CreatorTool>cjxl d=0.1 e=7</xmp:CreatorTool>
```

- **dc:Description:** Base64 ICC data (not human-readable, machine-parsable)
- **CreatorTool:** Encoding params (visible in Windows Properties)

→ See [docs/jxl_color_internals.md](docs/jxl_color_internals.md) for full technical details.

---

##  Recommended Settings

### Archival (Master Files)

```python
# tiff_to_jxl.py
CJXL_DISTANCE = 0.05      # Near-lossless, ~47MB for 45MP
#OR#
CJXL_DISTANCE = 0.1       # Also Near-lossless, ~34MB for 45MP

CJXL_EFFORT = 7           # Good compression speed tradeoff
EMBED_ICC_IN_JXL = True   # Always preserve ICC!
```

### Web / Delivery

```python
# tiff_to_jxl.py
CJXL_DISTANCE = 1.0       # Visually lossless, ~8MB
CJXL_EFFORT = 7

# jxl_to_tiff.py  
DJXL_OUTPUT_DEPTH = 8     # Smaller files
TIFF_COMPRESSION = "zip"
ADD_JPEG_PREVIEW = True   # Fast Explorer thumbnails
```

---

## Verifying ICC Preservation

```powershell
# After TIFF → JXL → TIFF round-trip:

# Check original ICC
exiftool -ProfileDescription -ProfileCopyright original.tif

# Check round-trip ICC
exiftool -ProfileDescription -ProfileCopyright roundtrip.tif

# Should match exactly!

# Check ICC is embedded in JXL
exiftool -XMP-dc:Description photo.jxl | findstr "ICC:"

# Check EXIF is visible in IrfanView
exiftool -Make -Model roundtrip.tif
```

---

## Known Limitations

### eciRGB v2 and Special ICC Profiles

The cjxl/dxjl converters were optimized for:
- sRGB (gamma ~2.2)
- Rec.2020 (standard gamma)
- Linear spaces

Profiles with special transfer curves like **eciRGB v2** (L* curve) may have slight color shifts during conversion because cjxl/djxl assumes standard gamma when encoding to XYB.

**Recommendation**: For critical work with eciRGB v2 or similar profiles, either:
- Keep originals in TIFF format, or
- Convert to Rec.2020 before JXL encoding

See [docs/jxl_color_internals.md](docs/jxl_color_internals.md) for technical details.

---

## Disclaimer

These tools were made for my personal workflow. 
Use at your own risk — I am not responsible for any issues you may encounter.

However, If you find any bugs, fell free to report to me - I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.

---

## More about this project
I am sharing these scripts because getting all of this to work correctly was unexpectedly difficult. The challenges were:

- Preserving 16-bit depth through the conversion pipeline
- Embedding EXIF so it is visible in IrfanView and other applications
- Correctly handling ICC profiles from Capture One exports (sRGB, AdobeRGB, ProPhoto RGB)
- **Fixing XMP overwrite bug that destroyed original metadata**
- **Fixing EXIF binary extraction that produced corrupted data**
- Sync mode — reconverting only re-exported photos in existing folders
- Performance — RAM usage, parallelism, and staging to minimize I/O

Getting there required finding and fixing several bugs that appears because of the specific combination of softwares I use (Capture One, cjxl, exiftool, IrfanView). Those bugs and their fixes are documented in [`docs/bugs_fixes_explained.md`](docs/bugs_fixes_explained.md).


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
