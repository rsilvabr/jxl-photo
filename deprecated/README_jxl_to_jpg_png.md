# jxl_to_jpg_png.py / jxl_to_jpg_png_terminal.ps1

Batch JXL → JPEG or PNG converter with ICC color profile conversion.
Decodes JXL files (including 16-bit ProPhoto RGB or Adobe-RGB files) and converts to sRGB JPEG or any target color space via ICC profile, ready for web, print, or client delivery.

---

## Requirements

```
Python 3.12+    (for jxl_to_jpg_png.py)
djxl     →  https://github.com/libjxl/libjxl/releases  (same package as cjxl)
magick   →  https://imagemagick.org  (ImageMagick 7+)
exiftool →  https://exiftool.org
```

**Important:** ImageMagick (`magick` command) must be installed on your system (not via pip).
Download from https://imagemagick.org and add to PATH.

The PowerShell terminal version (`jxl_to_jpg_png_terminal.ps1`) requires only djxl and magick (no Python).

---

## Quick start

```powershell
# Basic: sRGB JPEG, quality 95, output in sibling folder
py jxl_to_jpg_png.py "F:\2024\session\_EXPORT\16B_JXL"

# Rename ProPhotoRGB → sRGB in output filenames
py jxl_to_jpg_png.py "F:\2024" --rename-from "ProPhotoRGB" --rename-to "sRGB"

# Custom ICC profile (AdobeRGB, etc.)
py jxl_to_jpg_png.py "F:\2024" --icc-profile "C:\icc\AdobeRGB1998.icc"

# 16-bit PNG for print workflows
py jxl_to_jpg_png.py "F:\2024" --format png --bit-depth 16

# Preview without converting
py jxl_to_jpg_png.py "F:\2024" --dry-run
```

### Terminal version (PowerShell)

Drop `jxl_to_jpg_png_terminal.ps1` into any folder containing JXLs, edit the settings at
the top, and run. Creates a `jpeg-srgb/` subfolder with the converted files.

```powershell
# From inside the JXL folder:
.\jxl_to_jpg_png_terminal.ps1
```

Edit at the top of the script:
```powershell
$OutputFolderName = "jpeg-srgb"   # output subfolder name
$Quality          = 95             # JPEG quality
$Workers          = 8              # parallel workers
$RenameFrom       = "ProPhotoRGB"  # string to replace in filename
$RenameTo         = "sRGB"         # replacement string
```

---

## Key settings

Edit at the top of `jxl_to_jpg_png.py`:

```python
OUTPUT_FORMAT   = "jpeg"    # "jpeg" or "png"
JPEG_QUALITY    = 95        # 1-100, ignored for PNG
BIT_DEPTH       = 8         # 8 (standard) or 16 (PNG only, for print)

OUTPUT_ICC      = None
# None → use ImageMagick's built-in sRGB (standard for web and most print)
# Set to a path for a specific ICC profile:
# Example: r"C:\icc\AdobeRGB1998.icc"

OUTPUT_FOLDER_MODE = 1
# 0 → subfolder inside the JXL folder
# 1 → sibling folder next to the JXL folder  [default]
# 2 → JXL folder name + suffix

OUTPUT_FOLDER_NAME   = "jpeg-srgb"  # folder name for modes 0 and 1
OUTPUT_FOLDER_SUFFIX = "_srgb"      # suffix appended for mode 2

FILENAME_REPLACE_FROM = ""          # string to replace in output filename
FILENAME_REPLACE_TO   = ""          # replacement string

STAGING_DIR = None
# Set to an SSD path to write output there first, then move in bulk.
# Useful when the JXLs are on a slow HDD.

USE_RAM = True
# True  → PNG intermediate from djxl stays in RAM, piped to magick (faster)
# False → PNG is written to disk first
```

---

## Output modes

| Mode | Output location | Example |
|------|----------------|---------|
| `0` | Subfolder inside JXL folder | `.../16B_JXL/jpeg-srgb/photo.jpg` |
| `1` | Sibling folder (default) | `.../session/jpeg-srgb/photo.jpg` |
| `2` | JXL folder name + suffix | `.../16B_JXL_srgb/photo.jpg` |

---

## CLI reference

```
py jxl_to_jpg_png.py <input> [options]

Arguments:
  input                 Input root folder (searched recursively for JXLs)

Options:
  --mode 0-2            Output folder mode (default: 1)
  --output-name NAME    Folder name for modes 0 and 1 (default: jpeg-srgb)
  --output-suffix SUF   Suffix for mode 2 (default: _srgb)
  --format jpeg|png     Output format (default: jpeg)
  --quality 1-100       JPEG quality (default: 95)
  --bit-depth 8|16      Output bit depth (default: 8; 16 forces PNG)
  --icc-profile PATH    Path to target ICC profile (default: built-in sRGB)
  --workers N           Parallel threads (default: CPU count, max 8)
  --staging PATH        Staging directory for output files
  --ram                 Keep PNG intermediate in RAM
  --overwrite           Overwrite existing output files
  --dry-run             Show what would be converted without converting
  --no-log              Terminal output only, no log file
  --rename-from STR     String to replace in output filename
  --rename-to STR       Replacement string
```

---

## Color space notes

JXL files decoded by djxl carry their embedded ICC profile (e.g. ProPhoto RGB).
ImageMagick uses this as the source profile when converting to the target color space.

With `OUTPUT_ICC = None` (default), magick converts to built-in sRGB — correct for
web, social media, and most consumer printing.

For professional printing or lab workflows that require AdobeRGB, provide the ICC file:
```python
OUTPUT_ICC = r"C:\icc\AdobeRGB1998.icc"
```

---

## Performance

| Setting | Recommendation |
|---------|----------------|
| Workers | Value between CPU core count and thread count |
| `USE_RAM_FOR_PNG` | `True` — saves ~200MB disk I/O per file |
| Staging folder | Set to a separate disk from the TIFFs — avoids simultaneous read/write, much faster on HDDs |

With `USE_RAM = True`, no intermediate PNG is written to disk — djxl pipes directly
to ImageMagick's stdin. The `--staging` option lets you write output to a fast SSD
and move in bulk to the final destination.

---

## Logs

```
<script_folder>/Logs/jxl_to_jpg_png/YYYYMMDD_HHMMSS.log
```

Disable with `--no-log`.

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
- [Kimi](https://www.kimi.com) (Moonshot AI), Claude (Anthropic), and MiniMax (MiniMax AI) for code assistance and technical discussion
