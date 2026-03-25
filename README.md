# jxl-tiff-jpeg-converter

Batch converter for 16-bit TIFF exports → JPEG XL and JXL → JPEG/PNG, with ICC color profile preservation and full EXIF metadata correctly embedded and visible in IrfanView, XnView MP, and other applications.

---

## Why this exists

This toolset was developed to integrate JPEG XL (JXL) into a Capture One workflow, since Capture One does not export in this format natively.

**Why JPEG XL?** Spectacular compression with no compromise on bit depth.

- Lossless 16-bit files much smaller than TIFF and TIFF with ZIP/Deflate
- Lossy 16-bit files — small files that retain full 16-bit tonal information, something no other common format achieves (JPEG is 8-bit, TIFF lossless is large)
- This is genuinely new: small lossy files, but with 16-bit color depth

Here is an example of the gains when using JXL with 45MP Nikon Z7 files:

| Format | Typical size (45MP, 16-bit) |
|--------|-----------------------------|
| TIFF 16-bit | ~260 MB |
| TIFF 16-bit ZIP/Deflate | ~245 MB |
| JXL 16-bit lossless | ~173 MB |
| JXL 16-bit lossy `d=0.05` | ~47 MB |
| JXL 16-bit lossy `d=0.1` | ~34 MB |
| JXL 16-bit lossy `d=0.5` | ~13 MB |
| JXL 16-bit lossy `d=1.0` (visually lossless) | ~8 MB |

I am sharing these scripts because getting all of this to work correctly was unexpectedly difficult. The challenges were:

- Preserving 16-bit depth through the conversion pipeline
- Embedding EXIF so it is visible in IrfanView and other applications
- Correctly handling ICC profiles from Capture One exports (sRGB, AdobeRGB, ProPhoto RGB)
- Sync mode — reconverting only re-exported photos in existing folders
- Performance — RAM usage, parallelism, and staging to minimize I/O

Getting there required finding and fixing **six bugs** that only appear together in this specific combination of software (Capture One, cjxl, exiftool, IrfanView). Those bugs and their fixes are documented in [`docs/bugs_fixes_explained.md`](docs/bugs_fixes_explained.md).

---

## Disclaimer

These tools were made for my personal workflow (with the help of Claude). Use at your own risk — I am not responsible for any issues you may encounter.
If you choose to use it and find any errors/bugs, please let me know.

---

## What's included

| File | Description |
|------|-------------|
| `tiff_to_jxl.py` | Batch TIFF → JXL converter (lossless or lossy) |
| `jxl_to_jpeg.py` | Batch JXL → JPEG/PNG with ICC profile conversion |
| `jxl_to_jpeg_terminal.ps1` | Quick PowerShell version — drop into any folder and run |
| `docs/bugs_explained.md` | All 6 bugs explained in detail |

---

## Requirements

**Python 3.12+** — https://www.python.org/downloads/
```powershell
pip install tifffile numpy
```

**cjxl / djxl** — https://github.com/libjxl/libjxl/releases → `jxl-x64-windows-static.zip`
(`djxl` is only needed for `jxl_to_jpeg.py`)

Extract to `C:\tools\libjxl\` and add to PATH:
```powershell
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", $p + ";C:\tools\libjxl\bin", "User")
```

**ExifTool** — https://exiftool.org → rename the executable to `exiftool.exe` and add to PATH.

**ImageMagick** (for `jxl_to_jpeg.py` only) — https://imagemagick.org

Verify everything is working:
```powershell
cjxl --version      # JPEG XL encoder v0.11.x
djxl --version      # JPEG XL decoder v0.11.x
exiftool -ver       # 13.xx
magick --version    # ImageMagick 7.x
```

**Powershell** (for `jxl_to_jpeg_terminal` version) — Tested with PowerShell 7.6.0 version.



---

## Typical workflow

```
Capture One
    ↓ Export 16-bit TIFF (sRGB, AdobeRGB, ProPhoto RGB)

tiff_to_jxl.py      TIFF → JXL  (archive, stays 16-bit, lossless or lossy)
    ↓
    JXLs on disk — ~8–47MB each for lossy, ~173MB for lossless (45MP example)
    ↓
jxl_to_jpeg.py      JXL → JPEG  (when needed for print or delivery)
                                  ICC profile conversion applied here
```

## After conversion
Depending on your needs, two common approaches:

1. Keep both TIFF and JXL — exclude the TIFF export folders from backups to save space. Tools like FreeFileSync support folder filters that make this easy.
2. Delete TIFFs, keep only JXL — a separate script for this can be found here: [delete-tiff-exports](https://github.com/rsilvabr/delete-tiff-exports)

---

## tiff_to_jxl.py — quick start

```powershell
# Mode 0 — flat folder: input → output
py tiff_to_jxl.py "F:\Photos\TIFF" "F:\Photos\JXL"

# Mode 5 — Capture One _EXPORT workflow (most common)
py tiff_to_jxl.py "F:\2024" --mode 5

# Sync — re-exported from Capture One? Only reconvert what changed
py tiff_to_jxl.py "F:\2024" --mode 5 --sync

# 16 parallel workers
py tiff_to_jxl.py "F:\2024" --mode 5 --workers 16
```

### Distance (quality) guide

Set `CJXL_DISTANCE` at the top of the script:

| Distance | Description | Typical size (45MP, 16-bit) |
|----------|-------------|------------------------------|
| `0` | Mathematically lossless | ~173 MB |
| `0.1` | Near-lossless, imperceptible | ~34 MB |
| `0.5` | High quality — recommended starting point | ~13 MB |
| `1.0` | "Visually lossless" per libjxl docs | ~8 MB |

JXL lossy 16-bit retains full 16-bit tonal information, stored internally as 32-bit float. This is fundamentally different from JPEG, which is 8-bit only.

→ [Full documentation for tiff_to_jxl.py](docs/README_tiff_to_jxl.md)

---

## jxl_to_jpeg.py — quick start

```powershell
# Basic — sRGB JPEG, quality 95, output in sibling folder
py jxl_to_jpeg.py "F:\2024\session\_EXPORT\16B_JXL"

# Rename ProPhotoRGB → sRGB in filenames
py jxl_to_jpeg.py "F:\2024" --rename-from "ProPhotoRGB" --rename-to "sRGB"

# AdobeRGB output (ICC profile path required)
py jxl_to_jpeg.py "F:\2024" --icc-profile "C:\icc\AdobeRGB1998.icc"

# 16-bit PNG (for print workflows)
py jxl_to_jpeg.py "F:\2024" --format png --bit-depth 16

# Preview without converting
py jxl_to_jpeg.py "F:\2024" --dry-run
```

### Output modes

| Mode | Result | Example |
|------|--------|---------|
| `0` | Subfolder inside JXL folder | `.../16B_JXL/jpeg-srgb/photo.jpg` |
| `1` | Sibling folder (default) | `.../session/jpeg-srgb/photo.jpg` |
| `2` | JXL folder name + suffix | `.../16B_JXL_srgb/photo.jpg` |

→ [Full documentation for jxl_to_jpeg.py](docs/README_jxl_to_jpeg.md)

---

## Performance

| Setting | Recommendation |
|---------|----------------|
| Workers | Value between CPU core count and thread count |
| `USE_RAM_FOR_PNG` | `True` — saves ~200MB disk I/O per file |
| Staging folder | Set to a separate disk from the TIFFs — avoids simultaneous read/write, much faster on HDDs |

---

## The bugs

Getting IrfanView to show EXIF in a JXL converted from a Capture One TIFF required finding six independent issues:

1. **cjxl doesn't read TIFF** — and ImageMagick silently downgrades to 8-bit sRGB
2. **Capture One ICC 2-byte rounding error** — bytes 68-79 of the illuminant field are `0x...d32b` instead of the spec-required `0x...d32d`
3. **IrfanView reads JXL boxes linearly** — stops at the codestream, never reaches the Exif box
4. **Lossy JXL uses multiple `jxlp` boxes** — naive reorder function collapsed them into one
5. **Brackets in folder names break exiftool** — `[FINAL]` is treated as a wildcard
6. **Windows doubles TIFF count** — `rglob("*.tif")` and `rglob("*.TIF")` return the same files

→ [Full bug writeup with code](docs/bugs_fixes_explained.md)

---

## Known behavior — IrfanView and color-calibrated monitors

This is not a bug in the script, but it is worth documenting because it produces
confusing results.

JXL lossless files embed the ICC color profile as a blob. Most software handles this
correctly — GIMP, XnView MP, Darktable, Waterfox, and `jxl_to_jpeg.py` all
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

## Tested with

- Capture One Windows (ProPhoto RGB, AdobeRGB, sRGB — 16-bit, session exports)
- NX Studio exports (16-bit, uncompressed)
- Nikon D810 (36MP), Nikon Zf (24MP), Nikon Z7 (45MP), Nikon D200 (10MP), Fujifilm S5 Pro (6~12MP)
- cjxl v0.11.2, ExifTool 13.52, Python 3.12, Windows 11
- Paths with Japanese characters and `[brackets]`
