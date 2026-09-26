# jxl_photo — JXL Workflow Manager

Batch JPEG XL conversion tools with **full ICC color profile and EXIF metadata preservation**. Designed for photographers working with 16-bit TIFF files who want compact JXL archives without losing color accuracy or metadata. Tested with Capture One, Lightroom, NX Studio, Photoshop, and Fuji Hyper Utility exported 16-bit TIFFs.

---

## Contents

- [Why JPEG XL?](#why-jpeg-xl)
- [Features](#features)
- [Scripts](#scripts)
- [Requirements & Installation](#requirements--installation)
- [Quick Start — Interactive Wrapper](#quick-start--interactive-wrapper)
- [Auto Mode (since v1.3)](#auto-mode-since-v13)
- [Individual Scripts](#individual-scripts)
- [Recommended Settings](#recommended-settings)
- [Configuration File Location](#configuration-file-location)
- [ICC Preservation: How It Works](#icc-preservation-how-it-works)
- [Verifying ICC Preservation](#verifying-icc-preservation)
- [Behavior, Defaults & Known Limitations](#behavior-defaults--known-limitations)
- [Documentation](#documentation)
- [Notices for upgraders](#notices-for-upgraders)
- [Current version](#current-version)
- [Changelog](#changelog)
- [More about this project](#more-about-this-project)
- [Related project: a simpler, TIFF-only alternative](#related-project-a-simpler-tiff-only-alternative)
- [Disclaimer](#disclaimer)
- [License](#license)
- [Acknowledgments](#acknowledgments)

---

## Why JPEG XL?

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

The numbers behind these settings are measured and posted on r/jpegxl, on 16-bit ProPhoto exports from Capture One:

- [Distance, error and SNR](https://www.reddit.com/r/jpegxl/comments/1s6k718/analysis_jxl_distance_error_and_snr_analysis/): per-pixel SNR from `d=0.01` to `d=10` on a 45 MP photo. `d=1.0` is the sweet spot for pixels above 30 dB, `d=0.3–0.5` for 40 dB, and `d=0.05` for 50 dB: over 90% of the pixels stay above 50 dB, and not one pixel falls below 20 dB. That is where the archival setting comes from. A stress test in the same post pushed Shadows +100 and Blacks +100 in Capture One on decoded files, and showed no visible difference between lossless and `d=0.1`.
- [16-bit vs 8-bit, and JPEG](https://www.reddit.com/r/jpegxl/comments/1sp9qbj/analysis_jxl_distance_and_snr_16bit_vs_8bit_jpeg/): for lossy JXL an 8-bit file is **not** smaller than a 16-bit one, and it keeps less signal, so there is no reason to go 8-bit. JPEG needs 5–15× the file size to get close to JXL.
- [Generation loss](https://www.reddit.com/r/jpegxl/comments/1wlmfw0/my_jxl_archive_tool_refuses_bad_recompressions_i/): one recompression from an archived `d=0.1` down to `d=1.0` costs 0.02 dB, but reaching the same file size in many small steps costs up to 9 dB. That is why the recompressor goes to the target in one shot, counts generations, and refuses counterproductive re-encodes.

The per-pixel SNR analyzer used in the first two is [jxl-quality-analyzer](https://github.com/rsilvabr/jxl-quality-analyzer).

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
- **Resize + output sharpening** *(v2.3.0)*: `--resize-long/-short/-percent` and `--sharpen screen|print` (fitted to Capture One's presets) deliver a web/print JPEG at any size straight from the master
- JPEG preview embedding

### 4. **JXL → JXL recompression** *(v2.1.0)*
- Re-encode an existing JXL archive smaller (`cjxl in.jxl out.jxl -d X -e Y`): ICC, EXIF/XMP/IPTC and the `jxlphoto-*` provenance markers carried over, new parameters restamped
- **Counterproductive requests are caught**: each file's recorded `cjxl d=/e=` is compared against the request — same-distance and lower-distance asks fall back to a verbatim copy (default policy asks first; unattended runs fail closed to skip)
- **JPEG-recoverable JXLs (jbrd) are copied verbatim by default** — recompressing would destroy the bit-exact JPEG recovery and its MD5 binding
- **Keep-smaller net**: a re-encode that is not smaller than the source is replaced by the original bytes, so a run can never grow the archive
- `--delete-source` with the same gates as the other scripts (integrity at the final path, MD5 match for copies, optional `--verify-roundtrip`)
- **Colour-converted derivatives** *(v2.2.0)*: `--output-icc sRGB|AdobeRGB|<file.icc>` makes a light **16-bit** copy of a master in another colour space (e.g. a d=1.0 sRGB set that replaces the JPEG exports), with `--rename-from/--rename-to` to swap the profile name in the file names. A derivative is never written in place, never deletes, never overwrites anything that is not its own derivative, and never counts as proof that the original TIFF is archived
- **Resized / sharpened derivatives** *(v2.3.0)*: the same `--resize-*` and `--sharpen` recipes, combinable with `--output-icc`

### 5. **Professional Workflow Support**
- Multiple folder structure modes (flat, recursive, Capture One / Lightroom EXPORT workflows)
- Parallel processing (tested up to 32 workers)
- Sync mode (reconvert only changed files)
- Staging SSD support for large collections
- Manifests (CSV) for multi-folder batches, and named presets runnable unattended (`--run-preset`); since v2.3.0 each manifest row can carry its own colour space, size, sharpening and rename
- Choose the Capture One / Lightroom export output folder per run (`--export-jxl-folder`, *v2.2.0*) — e.g. masters in `_EXPORT/16B_JXL`, a separate print export in `_EXPORT/PRINT_JXL`, sRGB derivatives in `_EXPORT/16B_JXL_sRGB`

### 6. **Archive and replace** *(v2.0.0)*
- `--delete-source` in **every** mode — convert into a separate tree and drop the originals
- The source is removed only after its output is written to its **final** path, passes an integrity check there, and (with `--verify-roundtrip`) decodes back to the source pixels
- `--delete-skipped` finishes an archive interrupted between the conversion and the unlink
- Three confirmations before anything is deleted, the last one a time token that cannot be answered by reflex

### 7. **Provenance: which source made this output** *(v2.0.0)*
- The folder-collapsing modes let two files with the same name land on the same output. Every conversion records **which source it came from** (`jxlphoto-src` / `jxlphoto-srcsum` in XMP), so a later delete run refuses to overwrite one archive with an unrelated photo
- `--provenance path` (default, free) · `content` (survives folders you moved) · `adopt` (TIFF → JXL only: verifies and stamps an archive built before this existed, one time)
- A mismatch always fails closed: not converted, nothing overwritten, nothing deleted
- Lossless JXL → JPEG is bound to the JXL's **content**, not its name: `checksums.md5` now also stores the JXL's own MD5 (a `<name>.jxl-md5` companion line), and a delete run compares it — older databases fall back to `djxl --reconstruct_jpeg` (djxl ≥ 0.12), and when no proof can run the source is kept

### 8. **Multi-page and film scans**
- Split each page of a multi-page TIFF into its own JXL and reconstruct the original later — per-page ICC, bit depth, grayscale and `SubfileType` all restored (the IR page of a scan keeps its role)
- A split that arrives with **pages missing** is detected and its sources kept: the short TIFF it would produce is a perfectly valid file, so nothing downstream could tell

### 9. **Built for unattended runs**
- Exit codes: `0` success · `1` some files failed · `2` aborted (full disk, safety abort) · `3` you declined a confirmation
- `--summary-json` emits one machine-readable line per run; the wrapper consumes it to total a multi-entry manifest
- A full output volume stops the run instead of failing every remaining file one by one

---

> **⚠️ Upgrading from an earlier release?** Read the [notices for upgraders](#notices-for-upgraders) before running anything destructive: v2.3.0 fixes the colours of JXLs with table-curve ICC profiles (TIFFs decoded from them earlier are wrong), v2.2.0 refuses some TIFFs earlier releases archived wrongly, saved command lines changed behavior in v2.0.0, and JPEG → JXL archives made with v2.0.0–v2.0.3 may need the jbrd repair.

---

## Scripts

| Script | Purpose | Key Feature |
|--------|---------|-------------|
| [`jxl_photo.py`](jxl_photo.py) | Interactive wizard | Guided workflow with **Auto Mode** — analyzes folders and recommends best mode automatically |
| [`jxl_tiff_encoder.py`](jxl_tiff_encoder.py) | TIFF → JXL encoder | Embeds ICC in XMP for round-trip preservation; multi-page TIFF splitting |
| [`jxl_tiff_decoder.py`](jxl_tiff_decoder.py) | JXL → TIFF decoder | Restores original ICC from XMP using Roundtrip Mode; reconstructs multi-page TIFFs |
| [`jxl_jpeg_transcoder.py`](jxl_jpeg_transcoder.py) | JPEG ↔ JXL / JXL → PNG | Lossless transcoding, ICC conversion, PNG output |
| [`jxl_recompressor.py`](jxl_recompressor.py) | JXL → JXL recompressor | Shrinks an existing archive to a new distance/effort; refuses counterproductive re-encodes (copy/skip/ask), keeps metadata and provenance; colour-converted 16-bit derivatives (sRGB/AdobeRGB/any RGB ICC) |

---

## Requirements & Installation

### 1. Python 3.9+ and Packages

```powershell
# Install required packages
pip install tifffile numpy pillow rich imagecodecs
```

 **Important:** Install packages in the same Python version you'll use to run the scripts.

### 2. External Tools (Download Executables, NOT Source Code)

| Tool | Download URL | What to Download | Extract to |
|------|-------------|------------------|------------|
| **cjxl / djxl** | https://github.com/libjxl/libjxl/releases | `jxl-x64-windows-static.zip`   **(NOT `jxl-x64-windows.zip`)** | `C:\tools\libjxl\` or your choice |
| **exiftool** | https://exiftool.org | `exiftool-XX.XX_64.zip`  **(Windows .zip, NOT .tar.gz)** | `C:\tools\exiftool\` or your choice |
| **ImageMagick** | https://imagemagick.org | Installer `.exe` (Q16-HDRI x64) | Default location |

### Tested dependency versions

Versions used before and after the dependency update on 2026-07-12 (last tested commit: `f390463`):

| Component | Tested until commit `f390463` (2026-07-12) | Current (recommended) |
|---|---|---|
| libjxl (`cjxl`/`djxl`) | v0.11.2 | v0.12.0 |
| numpy | 2.4.3 | 2.5.1 |
| tifffile | 2026.3.3 | 2026.6.1 |
| Pillow | 12.1.1 | 12.3.0 |
| imagecodecs | 2026.3.6 | 2026.6.26 |
| rich | 14.3.3 | 15.0.0 |
| exiftool | 13.52 | 13.59 |
| ImageMagick | 7.1.2-17 | 7.1.2-27 |

Older versions may still work, but the current versions are what we test against.

> **libjxl v0.12:** the scripts auto-detect the `cjxl`/`djxl` version and adapt — lossless JPEG recovery uses `djxl --reconstruct_jpeg` (authoritative lossless guarantee), and pixel encodes can opt into `--buffering 0` (best compression, ~6× slower on large lossless TIFFs; see the [v1.8.0 benchmark](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.0)). On libjxl < 0.12 everything behaves as before; no v0.12-only flag is ever passed.
>
> On v0.12 the default path streams instead of buffering the whole image, so RAM per worker is modest: measured **0.99 GB** for a 24 MP file, **1.55 GB** at 45 MP and **3.32 GB** for a 93 MP scan (lossless, effort 9). Effort barely moves memory — megapixels do, at roughly 35–40 MB per megapixel per worker. Those figures are 16-bit input; 8-bit encodes 3–7× faster but uses only 4–21 % less memory, so it does not buy you extra workers. See [RAM per worker](docs/README_jxl_tiff_encoder.md#ram-per-worker) before raising `--workers`.

#### Common Download Mistakes

| Wrong Download | Why It Fails | Correct Download |
|---------------|--------------|------------------|
| `jxl-x64-windows.zip` | Only DLLs, no executables | `jxl-x64-windows-static.zip` |
| `exiftool-XX.XX.tar.gz` | Perl source code, needs Perl installed | `exiftool-XX.XX_64.zip` (Windows executable) |

#### exiftool Setup

> **No renaming needed:** the scripts detect both `exiftool.exe` and `exiftool(-k).exe`.

The Windows download comes as `exiftool(-k).exe`. If you prefer the plain name anyway:

```powershell
# Option A: Rename the file
Rename-Item "C:\tools\exiftool\exiftool(-k).exe" "exiftool.exe"

# Option B: Duplicate and rename (keeps the original)
Copy-Item "C:\tools\exiftool\exiftool(-k).exe" "C:\tools\exiftool\exiftool.exe"
```

The `(-k)` suffix means "keep console open" — the original behavior. Either name works.

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

You should see: `[✓] cjxl/djxl | [✓] exiftool | [✓] magick | [✓] tifffile | [✓] pillow | [✓] imagecodecs | [✓] rich`

### Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| `cjxl` not recognized | Downloaded `jxl-x64-windows.zip` (runtime DLLs only) | Download `jxl-x64-windows-static.zip` |
| `exiftool` not recognized at the prompt | Its folder is not on PATH, or the file is still named `exiftool(-k).exe` — the scripts accept that name, your shell does not | Add the folder to PATH (step 3) and reopen the terminal; rename or copy to `exiftool.exe` if you want the bare command to work too |
| `exiftool` returns nothing | Downloaded `.tar.gz` (Perl source) | Download `.zip` with `_64` suffix |
| `ModuleNotFoundError` | Packages in different Python version | Run `python -m pip install tifffile numpy pillow rich` |
| PATH not working | Terminal not restarted | Close and reopen PowerShell completely |

### Setup feels heavy?

There is a simpler alternative that needs no libjxl — only ImageMagick and ExifTool (plus Python 3.9+ if you use the optional wizard UI): [tiff-workflow](https://github.com/rsilvabr/tiff-workflow), a PowerShell toolkit that losslessly re-compresses TIFFs with ZIP/Deflate. Much less compression than JXL, but far less to install — [side-by-side numbers at the end of this README](#related-project-a-simpler-tiff-only-alternative).

---

## Quick Start — Interactive Wrapper

The easiest way to use this toolkit. Run `py jxl_photo.py` and follow the guided menu:

```
╭───────────────────────────────────────────── JXL Tools Environment ────────────────────────────────────────────────╮
│ [✓] cjxl/djxl | [✓] exiftool | [✓] magick | [✓] tifffile | [✓] pillow | [✓] imagecodecs | [✓] rich                    │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭───────────────────────────────────────────────────── Main Menu ────────────────────────────────────────────────────╮
│  1  New workflow                                                                                                   │
│  2  Repeat last workflow (unknown)                                                                                 │
│  3  Check dependencies again                                                                                       │
│  4  Edit default settings                                                                                          │
│  5  Reset all settings                                                                                             │
│  6  Move settings file                                                                                             │
│  7  Presets (2 saved)                                                                                              │
│  8  Repair JPEG recovery (jbrd audit/repair)                                                                       │
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

> **Tip:** The value shown in **blue** (or between `[brackets]`/`(parentheses)`) is the default. Just press `Enter` to accept!
>
> Example:
> ```
> Workers [4]:          ← Press Enter to use 4
> Distance [0.1]:       ← Press Enter to use 0.1
> Execute? [y/n] (y):   ← Press Enter to accept 'y' (yes)
> ```

---

## Auto Mode (since v1.3)

> Auto Mode reads your folder structure and *recommends* a mode — it never runs anything you have not confirmed. The recommendation fits common layouts; when it does not match what you had in mind, pick the mode yourself with **[N]**.

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
Source,Destination,Mode,Direction
F:\2025\Recife\_Export\TIFF,F:\2025\Recife\_Export\TIFF,6,tiff2jxl
F:\2025\São Paulo\_EXPORT\16bit,F:\2025\São Paulo\_EXPORT\16B_JXL,7,tiff2jxl
# F:\2025\横浜\RAW,F:\2025\横浜\JXL,0,tiff2jxl
```

- Edit paths, delete rows, reorder
- Comment with `#` to skip
- Rerun same manifest anytime
- Paths may contain spaces and non-ASCII characters — the file is written as UTF-8 with a BOM so Excel keeps them. If Excel re-saves it in the system ANSI codepage the manifest is refused, not guessed at: re-save with *CSV UTF-8 (comma delimited)* or regenerate it
- The `Direction` column binds the manifest to the workflow that generated it — running it from a different direction (e.g. a `tiff2jxl` manifest in a `jxl2tiff` session) is refused with a clear error instead of running the wrong script. Manifests without the column (older format) still run, with a warning.
- **Destination column:** only modes **0 and 2** honor it. Modes 1/3/4/5/6/7/8 compute their own output locations from each script's settings (`16B_JXL`, `converted_jxl`, ...) — the wrapper prints a warning when a manifest entry's Destination is ignored.
- **Per-row option columns (optional):** `jxl2jxl`, `jxl2jpeg` and `jxl2png` manifests carry `OutputICC, Resize, Sharpen, RenameFrom, RenameTo` after `Direction` — a filled cell overrides the wizard's answer for that row only (`sRGB`, `long:2048`, `print`, `ProPhoto`→`sRGB`, ...). Any invalid value refuses the whole manifest before anything runs; the per-row derivative rules (never in place, never deleting) are enforced per row. See [Running a list of folders](docs/README_jxl_tools.md#running-a-list-of-folders-manifest).
- **Manifest compatibility:** manifests are guaranteed to work with the version that generated them. Backward compatibility with older 2-column manifests is not guaranteed; regenerate the manifest if upgrading from a previous version.

---

## Individual Scripts

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

# Multi-page TIFF: split each real page into separate JXLs
py jxl_tiff_encoder.py "F:\Photos\2024" --mode 2 --multipage-mode split --thumbnail-mode exclude

# Multi-page TIFF: also export embedded thumbnails
py jxl_tiff_encoder.py "F:\Photos\2024" --mode 2 --multipage-mode split --thumbnail-mode include
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

# Reconstruct multi-page TIFF and drop thumbnail pages
py jxl_tiff_decoder.py "F:\Photos\2024" --mode 2 --thumbnail-handling ignore
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

### JXL → JXL (recompress an archive smaller)

```powershell
# Shrink a near-lossless archive to "visually lossless"
py jxl_recompressor.py "F:\Photos\Archive" --mode 1 --distance 1.0

# Recursive, each subfolder gets its own JXL_recompressed/ (structure kept)
py jxl_recompressor.py "F:\Photos\Archive" --mode 3 --distance 1.0

# Recursive into ONE flat output folder (mode 2 honors the output positional;
# modes 3/5/6/7 compute their own folders and ignore it)
py jxl_recompressor.py "F:\Photos\Archive" "F:\Photos\Archive_small" --mode 2 --distance 2.0

# Simulate first — nothing written, copied or deleted
py jxl_recompressor.py "F:\Photos\Archive" --mode 1 --distance 1.0 --dry-run

# Replace the archive in place, deleting nothing until verified
py jxl_recompressor.py "F:\Photos\Archive" --mode 8 --distance 1.0 --verify-roundtrip

# Light 16-bit sRGB derivatives of the Capture One masters, profile swapped in the name
#   _EXPORT/16B_JXL/_DSC0013_ProPhoto-g22_v1.jxl -> _EXPORT/16B_JXL_sRGB/_DSC0013_sRGB_v1.jxl
py jxl_recompressor.py "F:\Photos\2025" --mode 7 --export-subfolder 16B_JXL --export-jxl-folder 16B_JXL_sRGB --output-icc sRGB --distance 1.0 --sync --rename-from ProPhoto-g22 --rename-to sRGB
```

A derivative is built from the master JXL, not the TIFF — measured on a real ProPhoto export, the extra generation costs 0.017 dB against a direct encode of the TIFF, at the same size. See [Colour-converted derivatives](docs/README_jxl_recompressor.md#colour-converted-derivatives---output-icc).

### After conversion
Depending on your needs, three common approaches:

1. Keep both TIFF and JXL — exclude the TIFF export folders from backups to save space. Tools like FreeFileSync support folder filters that make this easy.
2. Delete TIFFs, keep only JXL — a separate script for this can be found here: [delete-tiff-exports](https://github.com/rsilvabr/delete-tiff-exports)
3. Use the configurable option to delete TIFFs after conversion available in this script.

---

## Recommended Settings

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

## ICC Preservation: How It Works

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
<dc:description>gen=1 | cjxl d=0.1 e=7</dc:description>
```

- **xmp:CreatorTool:** Base64 ICC data with "ICC:" prefix (for round-trip preservation)
- **dc:Description:** the encode record — an append-only lineage chain `gen=N | cjxl d=X e=Y | ...` where `gen=N` counts the lossy generations (visible in Windows Properties; any user caption stays first)

→ See [docs/jxl_color_internals.md](docs/jxl_color_internals.md) for full technical details.

---

## Verifying ICC Preservation

```powershell
# After TIFF → JXL → TIFF round-trip:

# Check original ICC
exiftool -ProfileDescription -ProfileCopyright original.tif

# Check round-trip ICC
exiftool -ProfileDescription -ProfileCopyright roundtrip.tif

# Should match exactly!

# Check ICC is embedded in JXL (ICC lives in XMP CreatorTool)
exiftool -XMP-xmp:CreatorTool photo.jxl | findstr "ICC:"

# Check EXIF is visible in IrfanView
exiftool -Make -Model roundtrip.tif
```

---

## Behavior, Defaults & Known Limitations

How the tools behave by default, where other software does not follow along, and the limits worth knowing before a large batch.

### Multi-page TIFFs: every page is converted by default

`--multipage-mode` defaults to **`split`** — one JXL per page (`photo.jxl`, `photo_page1.jxl`, ...), rejoined into a multi-page TIFF on decode. A TIFF with a single real page produces exactly `photo.jxl`, so for ordinary photos this is indistinguishable from the old default.

**This default changed.** It used to be `ignore` (page 0 only, everything else discarded), which caught people out: plenty of TIFFs are multi-page without looking like it — Capture One and many scanners append an embedded preview, and film scanners add an IR/mask page. Worse, mode 8 then deleted the source after encoding page 0, destroying the other pages permanently.

Embedded **thumbnail/preview** pages are still dropped by default (`--thumbnail-mode exclude`); they are reduced-resolution copies of a page that is already in the output. Add `--thumbnail-mode include` if you want the decoded TIFF to reproduce the original page structure exactly.

```powershell
py jxl_tiff_encoder.py "F:\Photos"                             # split, thumbnails dropped
py jxl_tiff_encoder.py "F:\Photos" --thumbnail-mode include    # keep the previews too
py jxl_tiff_encoder.py "F:\Photos" --multipage-mode ignore     # old behavior: page 0 only
```

In the wizard the setting lives under **Advanced Options** (Step 6A — answer `y` when asked "Configure advanced options?"), and the Step 7 summary spells out the policy before you type YES. If you do choose a page-dropping policy, the encoder reports the totals in the run summary — and **mode 8 refuses to delete any source whose pages were dropped**, so you cannot lose them by accident.

### The decoder never overwrites an original TIFF

The encoder's default mode 0 leaves `photo.tif` and `photo.jxl` side by side, and the JXL is newer. A sync decode into that same folder used to overwrite the **original master** with the decode. The decoder now only overwrites TIFFs it wrote itself (they carry its `jxlphoto-src` marker); any other TIFF is **refused** — not decoded over, and its JXL never deleted — and listed at the end of the run. Decode into another folder (`--mode 1`/`3`) or pass `--overwrite` if replacing it is really intended. Details: [decoder README](docs/README_jxl_tiff_decoder.md#original-tiff-masters-are-never-overwritten).

### Lossy is the default: `--distance 0.1`

The default is **near-lossless, not lossless**. At `d=0.1` a 45 MP file drops to roughly a tenth of the TIFF size, and a difference blend in Photoshop *will* show small deviations — that is the compression working as configured, not a bug. For a bit-exact archive use `--distance 0` (still ~40% smaller than an uncompressed TIFF). Note that `--mode` (0–8) only decides *where* output files go; quality is `--distance` alone.

#### The distance floor depends on your cjxl: 0.05 on libjxl 0.12, 0.01 on 0.11

**cjxl 0.12 clamps every lossy distance at or below 0.05 to the same value.** On two real 16-bit photos, `--distance 0.005` through `0.05` produced a **byte-identical** file. The menu and the CLI accept anything from 0 to 15, so setting `0.02` looks like it buys you something, but on 0.12 it does not: either stay at `0.05`, or go to `--distance 0` for true lossless. The floor was added in libjxl 0.12 ([PR #4238](https://github.com/libjxl/libjxl/pull/4238)): below it the DC coefficients leave the `int16` range, so the file no longer conforms to Level 5 of the JPEG XL spec. libjxl itself still decodes such files, but a decoder built for Level 5 may not.

**cjxl 0.11.2 does not clamp at 0.05.** Its floor is 0.01, and the distances between 0.01 and 0.05 are real steps. Measured on the same photos (effort 7):

| distance | cjxl 0.11.2 | cjxl 0.12.0 |
|---|---|---|
| 0.005 | same file as 0.01 | same file as 0.05 |
| 0.01 | 30.5 MB, 60.4 dB PSNR | same file as 0.05 |
| 0.02 | 24.4 MB, 56.2 dB | same file as 0.05 |
| 0.05 | 17.4 MB, 51.7 dB | 17.7 MB, 51.9 dB |
| 0.1 | 13.2 MB, 48.9 dB | 13.4 MB, 49.0 dB |

At 0.05 and above both versions write nearly the same file (0.12 is ~1–2% bigger and ~0.2 dB better). Since v2.2.0 the scripts read the installed cjxl version: the dead-zone warning and the recompressor's "same distance" check use 0.05 on libjxl 0.12 and later, and 0.01 before it.

Measured ratios on real 16-bit ProPhoto photos already stored as Deflate TIFF (output ÷ source), so you can see where the curve actually bends:

| distance | 0 | 0.05 | 0.1 | 0.2 | 0.5 | 1.0 |
|---|---|---|---|---|---|---|
| camera files | 61–70% | 14–18% | 10–14% | 7–10% | 4–7% | 2–4% |

The spread across photos at one distance is 1.1×–2.1×, so treat any single number as an order of magnitude, not a promise.

#### 8-bit sources: lossless can be *smaller* than lossy

Counter-intuitive but reproducible across four real photos: at 8 bits, `--distance 0` produced a **smaller** file than `--distance 0.05` (38.6% vs 46.4% of source on one, 42.6% vs 47.8% on another). JXL's lossless mode is very efficient at 8 bits, while VarDCT at a very low distance carries overhead it cannot amortise. If your source is 8-bit and you want small files, measure before assuming lossy wins.

### Default re-run behavior differs per script

The TIFF encoder/decoder default to **smart sync** (reconvert when the source is newer than the existing output — there is no plain "skip existing" CLI mode), while the JPEG transcoder **skips existing outputs** by default. Use `--overwrite` (always) or `--sync` (source newer) to control it explicitly. See each script's README for details.

### Film scanners: IR channel / Digital ICE

If your scanner software (e.g. SilverFast, VueScan) uses the IR page as a hidden channel for Digital ICE / dust & scratch removal, converting the TIFF to JXL and back **may break that feature**. Those programs often rely on vendor-specific tags and exact page ordering beyond the standard TIFF `SubfileType`. This tool preserves the page as a standard grayscale `PAGE`, but the original scanner software may no longer recognize it as an IR mask. **Test with one file before batch-processing important film scans.**

### Scanner ICC profiles and lossy encoding

Scanner ICC profiles (e.g. SilverFast `SFprofT`) can cause `cjxl` to produce very dark images in lossy mode. The encoder works around this by not embedding the ICC in the intermediate PNG and restoring it into the reconstructed TIFF (see `--icc-png-strategy`). The JXL file may therefore display with shifted colors in some viewers, but the TIFF round-trip is accurate. For scanner workflows, treat **JXL as the backup container and the reconstructed TIFF as the final image**.

### Viewer quirks (not data loss)

| Viewer | Behavior | Why |
|--------|----------|-----|
| **Windows Explorer** | Thumbnails ignore the embedded EXIF thumbnail and are **not color-managed** — ProPhoto/Adobe RGB images look washed out | Limitation of Microsoft's JXL WIC codec |
| **IrfanView** | EXIF visible for TIFF → JXL, **hidden** for JPEG → JXL (lossless or lossy) | JPEG → JXL uses Brotli (`brob` box), which IrfanView cannot read |
| **IrfanView / XnView MP** | Wide-gamut JXL may look slightly muted on a calibrated monitor | Viewer rendering limitation — the file keeps the full gamut; decode back to TIFF to confirm |
| **XnView MP** | Shows `Color Profile: sRGB` for lossy JXL regardless of the real space | Lossy JXL stores compact numeric primaries, not an ICC blob; XnView falls back to an "sRGB" label |

For reliable EXIF and color, use **XnView MP** or **digiKam**. If an image looks *heavily* desaturated, that **is** a real bug — please report it.

### Matrix decode mode is 8-bit internally

The decoder's Matrix mode (`--matrix`, for color-space conversion via LittleCMS) quantizes pixels to 8-bit for the transform and scales the result back to 16-bit. Effective precision is 8 bits in that mode only — use **Roundtrip mode** (the default) for full 16-bit fidelity.

### eciRGB v2, scanner profiles and other table-curve ICC profiles

Most profiles have a native JPEG XL form (a pure gamma, sRGB, Rec.2020, DCI-P3…), and those round-trip correctly — ProPhoto, Elle's LargeRGB g2.2, Adobe RGB and Wide Gamut included, wide gamut intact. Profiles whose tone curve is a **table** (eciRGB v2's L* curve, scanner LUT profiles, ROMM RGB with its linear toe) have none: in lossy mode cjxl then stores the whole ICC, and djxl decodes such a file to **linear sRGB**, not to the original space.

The encoder's default `cautious` ICC strategy detects most of these and encodes them "skip" (verified correct round trips for eciRGB v2 and Epson scanner profiles; the JXL itself then shows wrong colours in viewers, because it is tagged sRGB). A table-curve profile that passes the cautious check is embedded as an ICC blob — and since bug #437 (2026-09-26) that case is handled everywhere: the decoder detects it from djxl's own `--icc_out`/`--orig_icc_out` output and decodes to float, CONVERTING to the original profile (51.9 dB measured, against 15.4 dB for the old paste-the-ICC behaviour), the derivative paths of the recompressor/transcoder do the same, and the cautious test now refuses "embed" for a lossy ICC blob whatever the brightness says. Decoding such a file needs ImageMagick on PATH; without it the decode fails closed instead of writing a wrong-colour TIFF. Measurements and the exact mechanism: [Lossy JXL with an ICC blob](docs/jxl_color_internals.md#lossy-jxl-with-an-icc-blob-what-djxl-returns-measured-2026-09-25).

---

## Documentation

| Document | Contents |
|----------|----------|
| [docs/README_jxl_tools.md](docs/README_jxl_tools.md) | Full documentation for the interactive wrapper |
| [docs/README_jxl_tiff_encoder.md](docs/README_jxl_tiff_encoder.md) | Full documentation for TIFF → JXL encoding |
| [docs/README_jxl_tiff_decoder.md](docs/README_jxl_tiff_decoder.md) | Full documentation for JXL → TIFF decoding |
| [docs/README_jxl_jpeg_transcoder.md](docs/README_jxl_jpeg_transcoder.md) | Full documentation for JPEG ↔ JXL / JXL → PNG |
| [docs/README_jxl_recompressor.md](docs/README_jxl_recompressor.md) | Full documentation for JXL → JXL recompression |
| [docs/jxl_color_internals.md](docs/jxl_color_internals.md) | Deep dive: XYB, ICC blobs vs primaries, troubleshooting |
| [docs/version_history.md](docs/version_history.md) | Detailed notes for all superseded releases |
| [deprecated/README_jxl_to_jpg_png.md](deprecated/README_jxl_to_jpg_png.md) | Deprecated — JXL → JPG/PNG (superseded by jxl_jpeg_transcoder.py) |

---

## Notices for upgraders

### ⚠️ Upgrading to v2.3.0: TIFFs decoded from JXLs with a table-curve ICC profile

Earlier releases decoded a **lossy** JXL whose ICC profile has a table tone curve (ROMM RGB with its linear toe, eciRGB v2, scanner LUT profiles, Photoshop "Dot Gain" grey) with **wrong colours**, and logged it as OK. The same went for recompressor derivatives and transcoder JPEG/PNG conversions of those files. The **JXLs themselves are fine**: re-decode them with v2.3.0.

- Most archives are not affected: Capture One / Lightroom exports in ProPhoto, Elle's LargeRGB, Adobe RGB or sRGB have a native JPEG XL form, and the encoder's default `cautious` strategy already encoded eciRGB v2 and scanner profiles as "skip", which decoded correctly.
- Affected are files whose table-curve profile passed the cautious test, or files encoded with `--icc-png-strategy always`. To check one: `djxl file.jxl out.png --icc_out=a.icc --orig_icc_out=b.icc` — the file is affected when `a.icc` and `b.icc` differ.
- Decoding those files now needs ImageMagick on PATH (without it the decode fails with an error instead of writing a wrong TIFF).
- The encoder re-tests every ICC profile once on its first run (the cautious cache format changed), so that run is a little slower.

### ⚠️ Upgrading to v2.2.0 from any earlier release: four things to check

The audits behind v2.2.0 found paths where earlier releases produced a wrong archive or deleted a source they should not have. v2.2.0 refuses or keeps in every one of them; what it cannot do is repair what an earlier run already did:

- **TIFFs in CIELAB, YCbCr or MINISWHITE photometric** were archived by the encoder as if they were RGB/MINISBLACK — wrong colours, or inverted tones — and reported as a clean encode. v2.2.0 refuses them with a per-file reason. Capture One / Lightroom / NX Studio exports are RGB and unaffected; if you archived scans or files from other tools, spot-check them before discarding the TIFFs.
- **`jxl_tiff_decoder.py --matrix --delete-source`** dropped the alpha channel and deleted the JXL anyway. Under `--matrix` the sources are now always kept.
- **`jxl_jpeg_transcoder.py --force-convert --distance 0 --delete-source`** broke the JPEG reconstruction data and deleted the original JPEG without testing it. The JXLs are still valid images; the bit-exact JPEG is not recoverable from them. `--repair-jbrd --dry-run` tests the reconstruction of every JXL in a folder and reports the ones that fail.
- **`jxl_recompressor.py --delete-source --delete-skipped` in modes 1/3** could delete a source JXL when a same-named output written from a *different* photo already existed. It now requires the provenance proof in every mode.

### ⚠️ JPEG → JXL archives made with v2.0.0 – v2.0.3: check them before discarding the JPEGs

Those versions wrote their provenance marker into the XMP of every JXL — including the lossless JPEG transcodes (`jbrd`). For a JPEG that already carried XMP (typical of Lightroom / Capture One exports) that makes `djxl --reconstruct_jpeg` **fail**: the original JPEG is no longer recoverable bit-exactly, and `--delete-source` deleted those JPEGs anyway. JPEGs without XMP were not affected. The fix stops writing markers into `jbrd` containers and proves the reconstruction before any JPEG is deleted. For existing archives:

```powershell
py jxl_jpeg_transcoder.py "F:\Photos" --repair-jbrd --dry-run   # audit only
py jxl_jpeg_transcoder.py "F:\Photos" --repair-jbrd             # repair
```

A repaired file reconstructs a JPEG with **identical image data**; only its metadata bytes differ from the original. See [Repairing broken JPEG reconstruction](docs/README_jxl_jpeg_transcoder.md#repairing-broken-jpeg-reconstruction---repair-jbrd).

### ⚠️ Coming from v1.9.1 or earlier? Two things changed under existing command lines in v2.0.0

**1. `--delete-source` now works in every mode.** In v1.9.1 it was `if DELETE_SOURCE and mode == 8` — outside mode 8 the flag was silently ignored. A saved command or script with `--mode 3 --delete-source` deleted **nothing** then and deletes the originals **now**.

**2. An archive made before this release can be refused.** Runs that delete sources in a folder-collapsing mode (2/4/5/6/7, and mode 0 with an output folder) now check that the existing output really came from the source about to replace it. Outputs written before v2.0.0 carry no such record, so they are refused rather than overwritten. For TIFF → JXL, `--provenance adopt` verifies and stamps them in a single pass; the decoder and the transcoder have no equivalent yet — use a structure-preserving mode (0/1/3/8) for those folders.

Read [Upgrading from v1.9.1](docs/version_history.md#upgrading-from-v191) before running anything destructive. Nothing about ordinary conversion changed: same pixels, same ICC, same metadata.

---

## Current version

**v2.3.0** (2026-09-26) — derivatives at any size, and a colour fix for table-curve ICC profiles:

- **Resize + output sharpening** — `--resize-long/-short/-percent` and `--sharpen screen|print` on the transcoder (JXL → JPEG/PNG) and on the recompressor's derivatives: a 2048 px sharpened sRGB JPEG straight from the ProPhoto master. The presets are fitted to Capture One's own.
- **Per-row manifest options** — one manifest can mix recipes: `OutputICC, Resize, Sharpen, RenameFrom, RenameTo` columns override the wizard per row.
- **`--icc-profile AdobeRGB` built into the transcoder** — no `.icc` file needed.
- **Fixed: lossy JXLs with a table-curve ICC profile decoded with wrong colours** (ROMM with toe, eciRGB v2, scanner LUTs). They now decode through a float intermediate and a real conversion (15.4 → 51.9 dB). See the [upgrade notice](#notices-for-upgraders).

Existing command lines and manifests keep working. **1710 tests.**

[What's new, in full](#changelog) · [Release history](#release-history) · [Notices for upgraders](#notices-for-upgraders)

---

## Changelog

### What's new — v2.3.0 (current stable)

**Released 2026-09-26.** Supersedes v2.2.0. Rounds 41–42 (bugs #436–#438), plus derivatives at any size.

#### New: resize + output sharpening

`--resize-long PX`, `--resize-short PX` or `--resize-percent P`, and `--sharpen none|screen|print`, on two scripts:

- **jxl_jpeg_transcoder.py**, JXL → JPEG/PNG: a delivery file at any size from the master (`--resize-long 2048 --sharpen screen --quality 92`);
- **jxl_recompressor.py**, JXL → JXL: `--output-icc`, resize and sharpen make the run a derivative, and the recipes combine.

Aspect ratio is always kept, and nothing is enlarged without `--allow-upscale`. Sharpening works on the Lab **L** channel only (no colour fringes). The presets are fitted to Capture One's defaults on real Nikon Z7 exports at 1000–3000 px and full size: `print` is within 0.1 dB of the per-size best everywhere. A resized/sharpened file is a **derivative** with every guarantee `--output-icc` has: never in place, never deletes, never overwrites a non-derivative, and it carries `jxlphoto-derived:<recipe>` instead of the provenance markers, so changing the recipe re-derives on the next sync.

#### New: per-row options in manifests

`jxl2jxl`, `jxl2jpeg` and `jxl2png` manifests carry five optional columns after `Direction`: `OutputICC, Resize, Sharpen, RenameFrom, RenameTo`. A filled cell overrides the wizard's answer for that row only, and an empty cell means "not applied". The generated CSV already has them (empty), and older manifests load unchanged. An invalid value refuses the whole manifest before anything runs, the confirmation screen shows each row's recipe, and the derivative rules (never in place, never deleting) are checked per row.

#### New: `--icc-profile AdobeRGB` in the transcoder

The same built-in Adobe RGB (1998)-compatible profile as the recompressor. Built-in names are now case-insensitive, and a missing `.icc` path is refused up front.

#### Fixed: table-curve ICC profiles decoded with wrong colours (#437, #438)

A profile whose tone curve is a **table** has no native JPEG XL form, so a lossy cjxl stores the whole ICC and djxl returns the pixels in **linear sRGB**. The decoder pasted the original ICC on them (15.4 dB, colours completely off, logged as OK), and the derivative paths did the same. Now the case is detected from djxl itself, and the pixels are decoded to 32-bit float — which keeps the colours outside sRGB that an integer PNG would clip — and **converted** to the original profile: **51.9 dB**, the same as a native-profile file. Grey masters are covered too. The encoder's cautious test now refuses to embed such a profile in a lossy JXL. The plain explanation: [why the fix goes through a float decode](docs/jxl_color_internals.md#why-the-fix-goes-through-a-float-decode-the-plain-version).

#### Also fixed

- **#436:** a colour conversion of an **sRGB-encoded** JXL (`--to-srgb`/`--icc-profile`) re-tagged the pixels instead of converting them. The transcoder now always assigns the source profile explicitly.

Every fix has a regression test proven to fail against the pre-fix code. **1710 tests** in the suite.

---

### v2.2.0 — previous stable

**Released 2026-09-24, superseded by v2.3.0.** Supersedes v2.1.0 and the v2.1.1_beta1 pre-release (whose notes are in [version history](docs/version_history.md#v211_beta1)). Rounds 37–40 of auditing (bugs #347–#434), plus colour-converted derivatives.

#### New: colour-converted derivatives (`--output-icc`)

The recompressor can write a light copy of a master in another colour space instead of a plain recompression, e.g. the Capture One ProPhoto masters → a d=1.0 **sRGB** set that replaces the JPEG exports. The master is decoded at 16 bits, converted with ImageMagick from its **own** original ICC (the one the encoder keeps in XMP, the same profile the decoder restores) with relative colorimetric + black point compensation, and re-encoded at `--distance`, **still 16 bits**. There is no 8-bit option: an 8-bit lossy JXL measured no smaller.

- **Targets:** `sRGB`, `AdobeRGB` (a built-in Adobe RGB (1998)-compatible profile, identical to Adobe's own), or any RGB `.icc`.
- **Quality:** measured on a real 16 MP ProPhoto export, a d=1.0 sRGB derivative from a d=0.05 master lands **0.017 dB** from a direct encode of the TIFF, at the same size (4.1 MB vs a 17.8 MB master).
- **A derivative is disposable by construction:**
  - never written in place (modes 0/8 are refused);
  - never combined with `--delete-source`;
  - never overwrites a file that is not its own derivative, even with `--overwrite`, so pointing it at the master folder cannot hurt the masters;
  - re-derived when you change the target colour space;
  - `jxlphoto-src`/`srcsum` are removed, so a derivative can never pass as the archive of a TIFF (verified: the encoder with `--delete-source --delete-skipped` pointed at a derivative folder deletes nothing);
  - the ICC in its XMP is the **target** profile, so decoding a derivative to TIFF labels it correctly.
- **Guarded against silent colour errors:** the source profile is always assigned explicitly, and a converted image that has lost its ICC is refused instead of being encoded as sRGB.
- **`--rename-from/--rename-to`** swap the profile token in the output names, with the transcoder's semantics.
- **`--export-jxl-folder`** chooses the modes 6/7 output folder, also for the encoder (e.g. a separate `Print` export → `PRINT_JXL`).
- **Wrapper:** all three options are in the wizard (Step 5: output folder; Step 6: colour space + rename), in presets and in manifests.

#### Fixed: the distance floor follows the installed cjxl

The "floor at 0.05" was measured on cjxl 0.12 only. libjxl 0.12 added it ([PR #4238](https://github.com/libjxl/libjxl/pull/4238), to stay within Level 5 of the spec); cjxl 0.11.2 only clamps below 0.01, and there `d=0.01` is 60.4 dB PSNR against 51.7 dB at `d=0.05`. The toolkit used 0.05 for every cjxl, so on 0.11 the dead-zone warning was false and the recompressor copied instead of shrinking a real `d=0.02` source. The floor is now read from `cjxl --version`: 0.05 from libjxl 0.12 on, 0.01 before it. See [the distance floor](#the-distance-floor-depends-on-your-cjxl-005-on-libjxl-012-001-on-011).

#### Fixed: rounds 37–40 (88 fixes)

Almost all in the safety layer (delete gates, provenance, dry runs, temp files). The conversion core changed only where it was wrong. The ones that lost or corrupted data:

- **Wrong archives, reported as clean:** TIFF CIELAB/YCbCr was archived as inverted RGB and MINISWHITE with inverted tones. All are refused now, along with the other unsupported photometrics.
- **Sources deleted without proof:**
  - `--force-convert --distance 0` broke jbrd JPEG recovery **and** its delete gate never tested it. Now the full reconstruction proof runs.
  - The recompressor's `--delete-skipped` in modes 1/3 deleted a source on the strength of an unrelated same-named output.
  - `--matrix --delete-source` dropped the alpha and deleted anyway.
  - The decoder's smart sync admitted a foreign master TIFF to `--delete-skipped`.
  - A multi-page marker-read failure zeroed the group veto (decoder and recompressor).
  - A keep-smaller copy skipped the MD5 proof.
  - A metadata-failed decode was promoted before its verdict.
- **Dry runs that lied:** they promised conversions and deletions the real run refuses or skips. Every preview now applies the same predicates as the real run.
- **Killed runs that poisoned sync:** outputs are written to a `.tmp` beside the final name and swapped in atomically after the integrity check. `checksums.md5` appends are locked across processes, and the wrapper's config is saved atomically.
- **Also fixed:**
  - `--repair-jbrd` now strips every marker pair;
  - JPEGs with > 64 KiB trailers are accepted by the toolkit's own gate;
  - manifests anchor relative paths on the CSV's folder and stop after a killed child;
  - the wrapper keeps the recompressor policies;
  - the lineage `gen=` never counts down;
  - log files carry the pid;
  - plus a long tail of warnings for flags that were silently inert.

Full list: [bug tracking, rounds 37–40](docs/bug_tracking_since_v1.0.md). Every fix has a regression test proven to fail against the pre-fix code, many of them real-codec tests against real exports and film scans. **1576 tests** in the suite.

---

### Release history

| Version | Date | Highlights |
|---------|------|------------|
| **[v2.3.0](#changelog)** | 2026-09-26 | Resize + output sharpening for derivatives, per-row manifest options, transcoder AdobeRGB; table-curve ICC profiles decode with correct colours |
| [v2.2.0](#v220--previous-stable) | 2026-09-24 | Colour-converted 16-bit derivatives (`--output-icc`), `--export-jxl-folder`, distance floor per cjxl version; audits 37–40 (88 fixes) |
| [v2.1.1_beta1](docs/version_history.md#v211_beta1) | 2026-09-20 | Pre-release, superseded by v2.2.0 |
| [v2.1.0](docs/version_history.md#v210) | 2026-09-20 | New `jxl_recompressor.py`: shrink a JXL archive, refusing counterproductive re-encodes |
| [v2.0.3](docs/version_history.md#v203) | 2026-08-23 | JXL → JPEG delete gates bound to content, not names; 32 fixes |
| [v2.0.2](docs/version_history.md#v202) | 2026-08-19 | Re-archiving a multi-page scan no longer repeats a page |
| [v2.0.1](docs/version_history.md#v201) | 2026-08-13 | Delete machinery audited against real scans; 6 fixes |
| [v2.0.0](docs/version_history.md#v200) | 2026-08-09 | Archive and replace: `--delete-source` in every mode, provenance markers |
| [v1.9.1](docs/version_history.md#v191) | 2026-08-02 | Mode 6/7 manifests start immediately (collision scan skipped where it cannot find anything) |
| [v1.9.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.9.0) | 2026-08-01 | Disk-space preflight, full-disk abort, scan progress, staging cleanup |
| [v1.8.4](docs/version_history.md#v184) | 2026-07-28 | `--run-preset NAME` runs a saved preset unattended |
| [v1.8.3](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.3) | 2026-07-28 | Named presets, repeatable manifests, configurable default distance, run summary |
| [v1.8.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.2) | 2026-07-27 | Audit + real-batch fixes; multi-page default is now `split` |
| [v1.8.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.1) | 2026-07-26 | Audit release: data-safety hardening, multi-page reconstruction v2 |
| [v1.8.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.0) | 2026-07-18 | libjxl v0.12 support, output integrity verification |
| [v1.7.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.2) | 2026-07-18 | Wrapper delete confirmation unstuck; Exif/XMP before the codestream |
| [v1.7.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.1) | 2026-07-13 | Cautious ICC strategy (round-trip test + cache), `.jfif`/`.jpe` support |
| [v1.7.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.0) | 2026-07-12 | Multi-page TIFF support |
| [v1.6.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.6.0) | 2026-07-05 | Audit-driven fixes: staging concurrency, wrapper routing, CMYK rejection |
| [v1.5.3](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.5.3) | 2026-04-15 | Full Auto Mode, PNG bit depth, 8-bit TIFF black-image fix |
| [v1.4](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.4) | 2026-04-11 | JXL → JPEG workflow |
| [v1.3](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.3) | 2026-04-11 | Auto Mode (beta), manifests, embedded JPEG thumbnail |
| [v1.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.2) | 2026-04-05 | Basic/None decode modes, ICC mode selector |
| [v1.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.1) | 2026-04-05 | D50 patch modes, metadata strip |
| [v1.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.0) | 2026-04-02 | First stable release — TIFF and JPEG → JXL with ICC preservation |

### Older history

- Release notes before v1.8.2: [v1.8.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.1) · [v1.8.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.0) · [v1.7.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.2) · [v1.7.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.1) · [v1.7.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.0)
- [Version history](docs/version_history.md) — detailed notes for all superseded releases
- [Bug Tracking (v1.0 → current)](docs/bug_tracking_since_v1.0.md) — bugs fixed since v1.0
- [New Features (v1.0 → current)](docs/new_features_since_v1.0.md) — genuinely new features
- [Code Quality & Refactoring](docs/code_quality_refactoring.md) — internal cleanups, compatibility backports, dead code

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

## Related project: a simpler, TIFF-only alternative

If this toolkit's setup is more than you want to deal with, [tiff-workflow](https://github.com/rsilvabr/tiff-workflow) is a PowerShell toolkit (with an optional Python wizard UI) that losslessly re-compresses TIFFs with ZIP/Deflate compression — pixel data stays identical, only the compression is re-optimized. It also covers related TIFF chores: copying EXIF from JPEG to TIFF (Fuji S3/S5 Pro workflow), diagnosing padded 16-bit files, and generating colour-managed sRGB thumbnails.

- **What you need:** PowerShell 7 (or Windows PowerShell 5.1), ImageMagick, ExifTool — Python 3.9+ with `rich` only if you want the wizard UI
- **What's NOT needed:** Python or Python packages for the direct PowerShell usage, libjxl (cjxl/djxl)

| Format | 16-bit Size | 8-bit Size |
|--------|-------------|------------|
| Uncompressed TIFF | ~260 MB | ~130 MB |
| **ZIP/Deflate (PowerShell)** | ~220 MB (~15% smaller) | ~65 MB (~50% smaller) |
| JXL lossless (this toolkit) | ~173 MB (~35% smaller) | ~43 MB (~67% smaller) |
| **JXL lossy d=0.1 (this toolkit)** | ~34 MB (~87% smaller) | ~8 MB (~94% smaller) |

**Trade-off:** easier to install, but much less compression, and the output stays a TIFF. Still better than nothing. Once you are comfortable with ImageMagick and ExifTool, the setup here is the same two tools plus Python and libjxl.

---

## Disclaimer

These tools were made for my personal workflow.
Use at your own risk — I am not responsible for any issues you may encounter.

However, If you find any bugs, feel free to report to me - I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.

---

## License

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation  
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling  
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O  
- [Kimi](https://www.kimi.com) (Moonshot AI) and [Claude](https://www.anthropic.com/claude) (Anthropic) for code assistance and technical discussion
