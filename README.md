# JXL-TIFF-JPEG-PNG Converter

JPEG XL conversion tools with **full ICC color profile preservation** and full EXIF metadata correctly embedded and visible in IrfanView, XnView MP, and other applications.
Designed for enthusiasts and photographers working with 16-bit TIFF files who desire to compact their photos keeping 16-bit tonal range. Tested with Capture One, Lightroom, NX Studio, Photoshop and Fuji Hyper Utility exported 16-bit TIFFs.

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
The standout feature of this toolkit.
Special care was taken when developing this to don't break ICC profiles, so photos using AdobeRGB, ProPhotoRGB or other special ICC profiles will be correct after conversion.

When converting TIFF → JXL → TIFF, or JXL → JPG/PNG 

- **Lossless JXL:** ICC preserved automatically in JXL container
- **Lossy JXL:** Original ICC embedded as XMP metadata, then restored on decode
- **Result:** Your exact original ICC profile (gamma curves, copyright, device calibration)

Why this matters: Lossy JXL normally converts ICC to "native primaries" — 
which works for display but loses TRC detail important for professional editing.

Encoding and decoding with this kit guarantees that metadata will be preserved. 

### 2. **JPEG Preview Embedding**
TIFF output includes a second page with JPEG-compressed preview for fast 
thumbnail generation in Windows Explorer and other file managers.

### 3. **Professional Workflow Support**
- 16-bit TIFF preservation
- Full EXIF/XMP metadata preservation
- Multiple folder structure modes (flat, recursive, Capture One or Lightroom EXPORT)
- Parallel processing (tested up to 32 workers)
- Sync mode (only reconvert changed files)

---

##  Scripts

| Script | Purpose | Key Feature |
|--------|---------|-------------|
| [`tiff_to_jxl.py`](tiff_to_jxl.py) | TIFF → JXL encoder | Embeds ICC in XMP for round-trip preservation |
| [`jxl_to_tiff.py`](jxl_to_tiff.py) | JXL → TIFF decoder | Restores original ICC from XMP, adds JPEG preview |
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
# Single file
py jxl_to_tiff.py "photo.jxl"

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
3. The script has an configurable option to delete TIFFs after conversion. 



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

### Without Preservation (standard JXL workflow)

```
TIFF (ProPhoto ICC with Kodak TRC curves)
    ↓ cjxl lossy
JXL (native primaries only - ICC detail lost!)
    ↓ djxl
TIFF (generic ICC generated from primaries)
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
    ↓ jxl_to_tiff.py
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
```

---

## Disclaimer

These tools were made for my personal workflow. 
Use at your own risk — I am not responsible for any issues you may encounter.

Always test with a small batch before processing important archives.

---

## More about this project
I am sharing these scripts because getting all of this to work correctly was unexpectedly difficult. The challenges were:

- Preserving 16-bit depth through the conversion pipeline
- Embedding EXIF so it is visible in IrfanView and other applications
- Correctly handling ICC profiles from Capture One exports (sRGB, AdobeRGB, ProPhoto RGB)
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
