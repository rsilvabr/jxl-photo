# jxl_photo — JXL Workflow Manager

Batch JPEG XL conversion tools with **full ICC color profile and EXIF metadata preservation**. Designed for photographers working with 16-bit TIFF files who want compact JXL archives without losing color accuracy or metadata. Tested with Capture One, Lightroom, NX Studio, Photoshop, and Fuji Hyper Utility exported 16-bit TIFFs.

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

## What's New in v1.5

### JXL → JPEG Auto-Detect Mode
**Smart per-file detection** for mixed JXL archives:
- Files **with jbrd box** → lossless transcoding (original JPEG recovered)
- Files **without jbrd** → lossy conversion with configurable quality
- Processes entire folders automatically, routing each file to the optimal method

### Optional JPEG Preview in TIFF Output
JXL → TIFF conversion now supports **disabling the embedded JPEG preview**:
```bash
python jxl_tiff_decoder.py folder/ --no-preview  # Smaller files, no preview
```
Default behavior unchanged (preview enabled for compatibility).

### Refined JXL → JPEG Options
Step 2 now offers three clear choices:
1. **JPEG Auto-Detect** — Recommended (auto-routes based on jbrd presence)
2. **JPEG Lossless** — Force lossless transcoding (requires jbrd)
3. **JPEG Lossy** — Force lossy conversion with quality/ICC control

---

## What's New in v1.3

### Auto Mode + Manifest System

> **Beta:** Auto Mode is functional but still being tested. If you encounter issues, use manual mode selection (options 0-8) which is fully stable.

**[A] Auto Mode** analyzes your folder structure and recommends the best organization mode automatically:
- Detects `_EXPORT`, `Export_Lightroom`, etc. (case-insensitive)
- Shows folder mapping preview before running
- Recommends mode with confidence level (high/medium/low)

**[P] Manifest CSV** — Generate, edit in Excel, then run:
```
[A] Auto Mode → [P] Generate manifest → Edit in Excel → [M] Run from manifest
```

- Edit paths, delete rows, reorder before running
- Comment out lines with `#` to skip temporarily
- Manifests saved in `manifests/` folder — rerun anytime
- Use with `--sync` to re-process only changed files

### Embedded JPEG Thumbnail in JXL (Optional)
Optional embedded 256px sRGB thumbnail in JXL files for fast preview in IrfanView, XnView, digiKam.
```bash
python jxl_tiff_encoder.py folder/ --embed-thumbnail
```
Adds ~20KB per file.

**Windows Explorer Note:** The current JXL WIC codec from Microsoft Store generates its own thumbnail and **ignores the embedded EXIF thumbnail**. Worse, it does so **without color management** — so if your image uses ProPhoto RGB or Adobe RGB, the thumbnail will show wrong/washed-out colors. This is a **Windows codec limitation, not a bug in this software**. Use IrfanView, XnView MP, or digiKam for accurate thumbnails.

**IrfanView Note:** EXIF display in JXL has limitations with this software:

| Source | JXL Type | EXIF in IrfanView | Why |
|--------|----------|-------------------|-----|
| **TIFF → JXL** | Lossless | ✅ Shows | Boxes reordered (Exif before codestream) |
| **JPEG → JXL** | Lossless | ❌ Hidden | Brotli compression (`brob` box) - IrfanView can't read |
| **JPEG → JXL** | Lossy | ❌ Hidden | Brotli compression (`brob` box) - IrfanView can't read |

For reliable EXIF viewing regardless of source, use **XnView MP** or **digiKam**.

---

## Features

### 1. **TIFF → JXL Encoding**
- 16-bit TIFF preservation (lossless or near-lossless JXL)
- **ICC profile preservation** — exact original ICC restored on round-trip, even for lossy JXL
- **EXIF/XMP metadata** — fully preserved and visible in IrfanView, XnView MP, and other applications
- JPEG preview embedding in output TIFF (fast Explorer thumbnails)

### 2. **JXL → TIFF Decoding**
- Three decode modes: **Roundtrip** (ICC-restored), **Basic** (for consumer JXLs), **Matrix** (color space conversion)
- JPEG preview embedding in output TIFF
- Sync mode — reconvert only changed files

### 3. **JPEG ↔ JXL Transcoding**
- JPEG → JXL lossless transcoding (pixel-perfect)
- JXL → JPEG/PNG with ICC color space conversion (sRGB, AdobeRGB, ProPhoto RGB)
- JPEG preview embedding

### 4. **Professional Workflow Support**
- Multiple folder structure modes (flat, recursive, Capture One / Lightroom EXPORT workflows)
- Parallel processing (tested up to 32 workers)
- Sync mode (reconvert only changed files)
- Staging SSD support for large collections

---

##  Scripts

| Script | Purpose | Key Feature |
|--------|---------|-------------|
| [`jxl_photo.py`](jxl_photo.py) | Interactive wizard | Guided workflow with **Auto Mode** — analyzes folders and recommends best mode automatically |
| [`jxl_tiff_encoder.py`](jxl_tiff_encoder.py) | TIFF → JXL encoder | Embeds ICC in XMP for round-trip preservation |
| [`jxl_tiff_decoder.py`](jxl_tiff_decoder.py) | JXL → TIFF decoder | Restores original ICC from XMP using Roundtrip Mode, adds JPEG preview |
| [`jxl_jpeg_transcoder.py`](jxl_jpeg_transcoder.py) | JPEG ↔ JXL / JXL → PNG | Lossless transcoding, ICC conversion, PNG output |


---

##  Quick Start — Interactive Wrapper

The easiest way to use this toolkit. Run `py jxl_photo.py` and follow the guided menu:

```
╭───────────────────────────────────────────── JXL Tools Environment ────────────────────────────────────────────────╮
│ [✓] cjxl/djxl | [✓] exiftool | [✓] magick | [✓] tifffile | [✓] pillow | [✓] rich                                 │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭───────────────────────────────────────────────────── Main Menu ────────────────────────────────────────────────────╮
│  1  New workflow                                                                                                   │
│  2  Repeat last workflow (unknown)                                                                                 │
│  3  Check dependencies again                                                                                       │
│  4  Edit default settings                                                                                          │
│  5  Reset all settings                                                                                             │
│  6  Move settings file                                                                                             │
│  0  Exit                                                                                                           │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

The wizard guides you through: Source format → Destination → Directory → Output mode → Parameters → Confirm.

**Example session:**
```
[1] New workflow
  Step 1: Source Format   → TIFF
  Step 2: Destination     → JXL d=0.1
  Step 3: Directory       → F:\Photos\2024
  Step 4: Mode            → 7 (Marker _EXPORT, subfolder)
  Step 5: Confirmation    → OK
  Step 6: Parameters      → Workers: 8, Distance: 0.1, Effort: 7
  Step 7: Summary         → Review and type YES to confirm
  → Executes the underlying script with all options
```

---

##  Auto Mode (New in v1.3)

> **Beta:** Auto Mode is new and being actively tested. It works well for common folder structures, but if you encounter unexpected recommendations, use manual mode selection (0-8) which is fully stable and tested.

Instead of memorizing modes 0-8, press **[A]** in Step 4. The wizard scans your folder and:

1. **Analyzes** structure (flat, recursive, export folders)
2. **Recommends** best mode with confidence level
3. **Previews** folder mappings (source → destination)

**Auto Mode detection:**
- `_EXPORT`, `Export_*` → Mode 6/7 (Capture One/Lightroom workflows)
- Deep subfolders → Mode 3 (recursive)
- Multiple folders → Mode 2 (flat output)
- Single folder → Mode 0 (in-place)

**After analysis, choose:**
- **[Y]** Accept and run
- **[P]** Generate manifest CSV → edit in Excel → **[M]** Run from manifest
- **[V]** View manifest (if exists)
- **[N]** Choose mode manually

### Manifest System

Generate a CSV to edit before running:
```csv
Source,Destination
F:\2025\Tokyo\_Export\TIFF,F:\2025\Tokyo\_Export\JXL
F:\2025\Kyoto\_EXPORT\16bit,F:\2025\Kyoto\_EXPORT\JXL
# F:\2025\Osaka\RAW,F:\2025\Osaka\JXL
```

- Edit paths, delete rows, reorder
- Comment with `#` to skip
- Rerun same manifest anytime

---

##  Individual Scripts

### Typical workflow (script commands)

```
Capture One
    ↓ Export 16-bit TIFF (sRGB, AdobeRGB, ProPhoto RGB)
jxl_tiff_encoder.py      TIFF → JXL  (archive, stays 16-bit, lossless or lossy)
    ↓
    JXLs on disk — ~8–47MB each for lossy, ~173MB for lossless (45MP example)
    ↓
jxl_tiff_decoder.py      JXL → TIFF  (when master TIFF is needed again)
    ↓ OR
jxl_jpeg_transcoder.py   JXL → JPEG/PNG  (when needed for print or delivery)
                                   ICC profile conversion applied here
```

### TIFF → JXL

```powershell
# Single file
py jxl_tiff_encoder.py "photo.tif"

# Folder (Capture One _EXPORT workflow)
py jxl_tiff_encoder.py "F:\Photos\2024" --mode 7

# With settings
py jxl_tiff_encoder.py "photo.tif" --mode 0 --workers 8
```

### JXL → TIFF

```powershell
# Single file — auto mode (Roundtrip if has ICC, Basic if not)
py jxl_tiff_decoder.py "photo.jxl"

# Force Matrix mode for color space conversion
py jxl_tiff_decoder.py "photo.jxl" --matrix --target-icc "C:\icc\sRGB.icc"

# Folder
py jxl_tiff_decoder.py "F:\Photos\2024" --mode 7

# 8-bit output for web
py jxl_tiff_decoder.py "photo.jxl" --depth 8
```

### JPEG ↔ JXL / JXL → PNG

```powershell
# JPEG → JXL (lossless transcoding)
py jxl_jpeg_transcoder.py "F:\Photos\2024"

# JXL → JPEG (auto: lossless recovery if jbrd present, else lossy)
py jxl_jpeg_transcoder.py "F:\Photos\2024" --mode 8

# JXL → PNG 16-bit (archival)
py jxl_jpeg_transcoder.py "F:\Photos\2024" --format png

# JXL → sRGB JPEG (ICC conversion via ImageMagick)
py jxl_jpeg_transcoder.py "F:\Photos\2024" --to-srgb --quality 95
```


### After conversion
Depending on your needs, three common approaches:

1. Keep both TIFF and JXL — exclude the TIFF export folders from backups to save space. Tools like FreeFileSync support folder filters that make this easy.
2. Delete TIFFs, keep only JXL — a separate script for this can be found here: [delete-tiff-exports](https://github.com/rsilvabr/delete-tiff-exports)
3. Use the configurable option to delete TIFFs after conversion available in this script. 



---

##  Documentation

| Document | Contents |
|----------|----------|
| [docs/README_jxl_tools.md](docs/README_jxl_tools.md) | Full documentation for the interactive wrapper |
| [docs/README_jxl_tiff_encoder.md](docs/README_jxl_tiff_encoder.md) | Full documentation for TIFF → JXL encoding |
| [docs/README_jxl_tiff_decoder.md](docs/README_jxl_tiff_decoder.md) | Full documentation for JXL → TIFF decoding |
| [docs/README_jxl_jpeg_transcoder.md](docs/README_jxl_jpeg_transcoder.md) | Full documentation for JPEG ↔ JXL / JXL → PNG |
| [docs/jxl_color_internals.md](docs/jxl_color_internals.md) | Deep dive: XYB, ICC blobs vs primaries, troubleshooting |
| [deprecated/README_jxl_to_jpg_png.md](deprecated/README_jxl_to_jpg_png.md) | Deprecated — JXL → JPG/PNG (superseded by jxl_jpeg_transcoder.py) |

---

## Requirements & Installation

### 1. Python 3.8+ and Packages

```powershell
# Install required packages
pip install tifffile numpy pillow rich
```

 **Important:** Install packages in the same Python version you'll use to run the scripts.

### 2. External Tools (Download Executables, NOT Source Code)

| Tool | Download URL | What to Download | Extract to |
|------|-------------|------------------|------------|
| **cjxl / djxl** | https://github.com/libjxl/libjxl/releases | `jxl-x64-windows-static.zip`   **(NOT `jxl-x64-windows.zip`)** | `C:\tools\libjxl\` or your choice |
| **exiftool** | https://exiftool.org | `exiftool-XX.XX_64.zip`  **(Windows .zip, NOT .tar.gz)** | `C:\tools\exiftool\` or your choice |
| **ImageMagick** | https://imagemagick.org | Installer `.exe` (Q16-HDRI x64) | Default location |

####  Common Download Mistakes

| Wrong Download | Why It Fails | Correct Download |
|---------------|--------------|------------------|
| `jxl-x64-windows.zip` | Only DLLs, no executables | `jxl-x64-windows-static.zip` |
| `exiftool-XX.XX.tar.gz` | Perl source code, needs Perl installed | `exiftool-XX.XX_64.zip` (Windows executable) |

#### exiftool Setup

> **Note (v1.3+):** The scripts now automatically detect both `exiftool.exe` and `exiftool(-k).exe`. Renaming is no longer required, but still works if you prefer.

The Windows download comes as `exiftool(-k).exe`. **For v1.2 and earlier, you need to rename it:**

```powershell
# Option A: Rename the file
Rename-Item "C:\tools\exiftool\exiftool(-k).exe" "exiftool.exe"

# Option B: Duplicate and rename (keeps the original)
Copy-Item "C:\tools\exiftool\exiftool(-k).exe" "C:\tools\exiftool\exiftool.exe"
```

The `(-k)` suffix means "keep console open" — the original behavior. The scripts now handle both names automatically.

### 3. Add to PATH (PowerShell)

**Replace the example paths below with YOUR actual installation paths:**

```powershell
# EDIT THESE PATHS to match where YOU extracted the tools:
$myPaths = @(
    "C:\tools\libjxl\bin",                           # where cjxl.exe and djxl.exe are
    "C:\tools\exiftool",                              # where exiftool.exe is (RENAMED!)
    "C:\Program Files\ImageMagick-7.1.1-Q16-HDRI"     # where magick.exe is
)

# Add to user PATH
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", ($myPaths -join ";") + ";$p", "User")

# RESTART your PowerShell/terminal after this!
```

### 4. Verify Installation

> **Important:** All tools must be in your PATH for both the wrapper and individual scripts to find them. The wrapper and scripts only search the system PATH — they do not look in other directories.

**Restart PowerShell**, then run:

```powershell
# Each should return a version number
cjxl --version          # Should show: cjxl v0.XX.X
exiftool -ver           # Should show: 12.XX or 13.XX
magick -version         # Should show: ImageMagick version
python -c "import tifffile, PIL, rich; print('All Python packages OK')"

# Test full environment
cd "C:\Users\YourName\Documents\GitHub\jxl-photo"  # adjust path
py jxl_photo.py
```

You should see: `[✓] cjxl/djxl | [✓] exiftool | [✓] magick | [✓] tifffile | [✓] pillow | [✓] rich`

### Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| `cjxl` not recognized | Downloaded `jxl-x64-windows.zip` (runtime DLLs only) | Download `jxl-x64-windows-static.zip` |
| `exiftool` not recognized | File still named `exiftool(-k).exe` (v1.2 and earlier) | For v1.3+, automatic detection works. For earlier versions, rename to `exiftool.exe` (see step 2) |
| `exiftool` returns nothing | Downloaded `.tar.gz` (Perl source) | Download `.zip` with `_64` suffix |
| `ModuleNotFoundError` | Packages in different Python version | Run `python -m pip install tifffile numpy pillow rich` |
| PATH not working | Terminal not restarted | Close and reopen PowerShell completely |

---

##  Alternative: Simpler Setup (TIFF lossless compression)

If the setup above feels overwhelming, there's an **easier alternative** that requires less installation:

### [convert_tiff_to_deflate](https://github.com/rsilvabr/convert_tiff_to_deflate)
A standalone PowerShell script that compresses TIFFs using ZIP/Deflate compression.

**What you need:**
- PowerShell 7 (or Windows PowerShell 5.1)
- ImageMagick
- ExifTool

**What's NOT needed:**
- Python or Python packages
- libjxl (cjxl/djxl)

**Compression comparison:**

| Format | 16-bit Size | 8-bit Size |
|--------|-------------|------------|
| Uncompressed TIFF | ~260 MB | ~130 MB |
| **ZIP/Deflate (PowerShell)** | ~220 MB (~15% smaller) | ~65 MB (~50% smaller) |
| JXL lossless (this toolkit) | ~173 MB (~35% smaller) | ~43 MB (~67% smaller) |
| **JXL lossy d=0.1 (this toolkit)** | ~34 MB (~87% smaller) | ~8 MB (~94% smaller) |

**Trade-off:** Easier to install, but less compression than JXL. Still better than nothing!

When you're comfortable with ImageMagick and ExifTool, come back here for JXL with much better compression.

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
    ↓ jxl_tiff_encoder.py (EMBED_ICC_IN_JXL = True)
JXL (native primaries + XMP with base64 ICC)
    ↓ jxl_tiff_decoder.py (Roundtrip Mode)
TIFF (original ProPhoto ICC restored!)
```

**Result:** Exact original ICC with all metadata intact.

### Technical Details

The ICC is base64-encoded and stored in XMP:

```xml
<xmp:CreatorTool>ICC:AAADrEtDTVMCEAAAbW50clJHQiBYWVog...</xmp:CreatorTool>
<dc:description>cjxl d=0.1 e=7</dc:description>
```

- **xmp:CreatorTool:** Base64 ICC data with "ICC:" prefix (for round-trip preservation)
- **dc:Description:** Encoding params `cjxl d=X e=Y` (visible in Windows Properties)

→ See [docs/jxl_color_internals.md](docs/jxl_color_internals.md) for full technical details.

---

##  Recommended Settings

### Archival (Master Files)

```python
# jxl_tiff_encoder.py
CJXL_DISTANCE = 0.05      # Near-lossless, ~47MB for 45MP
#OR#
CJXL_DISTANCE = 0.1       # Also Near-lossless, ~34MB for 45MP

CJXL_EFFORT = 7           # Good compression speed tradeoff
EMBED_ICC_IN_JXL = True   # Always preserve ICC!
```

### Web / Delivery

```python
# jxl_tiff_encoder.py
CJXL_DISTANCE = 1.0       # Visually lossless, ~8MB
CJXL_EFFORT = 7

# jxl_tiff_decoder.py
DJXL_OUTPUT_DEPTH = 8     # Smaller files
TIFF_COMPRESSION = "zip"
ADD_JPEG_PREVIEW = True   # Fast Explorer thumbnails
```

---

## Configuration File Location

The wrapper (`jxl_photo.py`) saves settings in `.jxl_tools_config.json`:

1. **First priority:** Script directory (where `jxl_photo.py` is located)
2. **Fallback:** User home directory (`%USERPROFILE%` on Windows, `~` on Linux/Mac)

This allows per-project configurations — place a config file in the script folder for project-specific settings, or use the user home for global defaults.

To move settings between locations: use option **6** in the main menu.

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

## Changelog

- [Bug Tracking (v1.0 → current)](docs/bug_tracking_since_v1.0.md) — bugs fixed since v1.0
- [New Features (v1.0 → current)](docs/new_features_since_v1.0.md) — genuinely new features

---

## Disclaimer

These tools were made for my personal workflow. 
Use at your own risk — I am not responsible for any issues you may encounter.

However, If you find any bugs, feel free to report to me - I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.

---

## More about this project
I am sharing these scripts because getting all of this to work correctly was unexpectedly difficult. The challenges were:

- Preserving 16-bit depth through the conversion pipeline
- Embedding EXIF so it is visible in IrfanView and other applications
- Correctly handling ICC profiles from Capture One exports (sRGB, AdobeRGB, ProPhoto RGB)
- Fixing XMP overwrite bug that destroyed original metadata
- Fixing EXIF binary extraction that produced corrupted data
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
- [Kimi](https://www.kimi.com) (Moonshot AI) and [MiniMax](https://www.minimax.io/) (MiniMax AI) for code assistance and technical discussion
