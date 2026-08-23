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

I have tested with different settings and posted on reddit, [click here](https://www.reddit.com/r/jpegxl/comments/1s6k718/edit_stress_test_lossy_jxl_under_heavy_editing/) and [here](https://www.reddit.com/r/jpegxl/comments/1sp9qbj/analysis_jxl_distance_and_snr_16bit_vs_8bit_jpeg/) to check. 

## Current version

**v2.0.3** (2026-08-23) — maintenance, with one fix that matters if you archive JPEGs as JXL and delete the originals: the lossless JXL → JPEG delete gates trusted the JXL's **name**, so a same-named JXL that was never archived could be deleted while the real one sat elsewhere. The gates now bind the file's **content**. Also here: an RGB ICC was being attached to grayscale output (every film-scan IR page) on the `--to-srgb` / `--icc-profile` paths, a failed move out of staging could delete a good pre-existing archive, and re-encoding a lost page of a pre-v2.0.2 multi-page archive split the group in two. 32 fixes in all across three audit rounds — the conversion path itself came out clean again, re-verified pixel-identical on the real fixtures.

[What changed, in full](#changelog) · [Release history](#release-history) · previous stable: [v2.0.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v2.0.2)

> ### ⚠️ Coming from v1.9.1 or earlier? Two things changed under existing command lines in v2.0.0
>
> **1. `--delete-source` now works in every mode.** In v1.9.1 it was `if DELETE_SOURCE and mode == 8` — outside mode 8 the flag was silently ignored. A saved command or script with `--mode 3 --delete-source` deleted **nothing** then and deletes the originals **now**.
>
> **2. An archive made before this release can be refused.** Runs that delete sources in a folder-collapsing mode (2/4/5/6/7, and mode 0 with an output folder) now check that the existing output really came from the source about to replace it. Outputs written before v2.0.0 carry no such record, so they are refused rather than overwritten. For TIFF → JXL, `--provenance adopt` verifies and stamps them in a single pass; the decoder and the transcoder have no equivalent yet — use a structure-preserving mode (0/1/3/8) for those folders.
>
> Read [Upgrading from v1.9.1](#upgrading-from-v191) before running anything destructive. Nothing about ordinary conversion changed: same pixels, same ICC, same metadata.

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
- Manifests (CSV) for multi-folder batches, and named presets runnable unattended (`--run-preset`)

### 5. **Archive and replace** *(v2.0.0)*
- `--delete-source` in **every** mode — convert into a separate tree and drop the originals
- The source is removed only after its output is written to its **final** path, passes an integrity check there, and (with `--verify-roundtrip`) decodes back to the source pixels
- `--delete-skipped` finishes an archive interrupted between the conversion and the unlink
- Three confirmations before anything is deleted, the last one a time token that cannot be answered by reflex

### 6. **Provenance: which source made this output** *(v2.0.0)*
- The folder-collapsing modes let two files with the same name land on the same output. Every conversion records **which source it came from** (`jxlphoto-src` / `jxlphoto-srcsum` in XMP), so a later delete run refuses to overwrite one archive with an unrelated photo
- `--provenance path` (default, free) · `content` (survives folders you moved) · `adopt` (TIFF → JXL only: verifies and stamps an archive built before this existed, one time)
- A mismatch always fails closed: not converted, nothing overwritten, nothing deleted
- Lossless JXL → JPEG is bound to the JXL's **content**, not its name: `checksums.md5` now also stores the JXL's own MD5 (a `<name>.jxl-md5` companion line), and a delete run compares it — older databases fall back to `djxl --reconstruct_jpeg` (djxl ≥ 0.12), and when no proof can run the source is kept

### 7. **Multi-page and film scans**
- Split each page of a multi-page TIFF into its own JXL and reconstruct the original later — per-page ICC, bit depth, grayscale and `SubfileType` all restored (the IR page of a scan keeps its role)
- A split that arrives with **pages missing** is detected and its sources kept: the short TIFF it would produce is a perfectly valid file, so nothing downstream could tell

### 8. **Built for unattended runs**
- Exit codes: `0` success · `1` some files failed · `2` aborted (full disk, safety abort) · `3` you declined a confirmation
- `--summary-json` emits one machine-readable line per run; the wrapper consumes it to total a multi-entry manifest
- A full output volume stops the run instead of failing every remaining file one by one

---

##  Scripts

| Script | Purpose | Key Feature |
|--------|---------|-------------|
| [`jxl_photo.py`](jxl_photo.py) | Interactive wizard | Guided workflow with **Auto Mode** — analyzes folders and recommends best mode automatically |
| [`jxl_tiff_encoder.py`](jxl_tiff_encoder.py) | TIFF → JXL encoder | Embeds ICC in XMP for round-trip preservation; multi-page TIFF splitting |
| [`jxl_tiff_decoder.py`](jxl_tiff_decoder.py) | JXL → TIFF decoder | Restores original ICC from XMP using Roundtrip Mode; reconstructs multi-page TIFFs |
| [`jxl_jpeg_transcoder.py`](jxl_jpeg_transcoder.py) | JPEG ↔ JXL / JXL → PNG | Lossless transcoding, ICC conversion, PNG output |


---

##  Quick Start — Interactive Wrapper

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

##  Auto Mode (since v1.3)

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
- **Manifest compatibility:** manifests are guaranteed to work with the version that generated them. Backward compatibility with older 2-column manifests is not guaranteed; regenerate the manifest if upgrading from a previous version.

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
| [docs/version_history.md](docs/version_history.md) | "What's New" notes for releases before v1.8 |
| [deprecated/README_jxl_to_jpg_png.md](deprecated/README_jxl_to_jpg_png.md) | Deprecated — JXL → JPG/PNG (superseded by jxl_jpeg_transcoder.py) |

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

####  Common Download Mistakes

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

There is a simpler alternative that needs no Python and no libjxl — only ImageMagick and ExifTool: [convert_tiff_to_deflate](https://github.com/rsilvabr/convert_tiff_to_deflate), a standalone PowerShell script that compresses TIFFs with ZIP/Deflate. Much less compression than JXL, but far less to install. [Side-by-side numbers at the end of this README](#related-project-a-simpler-tiff-only-alternative).

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

### Lossy is the default: `--distance 0.1`

The default is **near-lossless, not lossless**. At `d=0.1` a 45 MP file drops to roughly a tenth of the TIFF size, and a difference blend in Photoshop *will* show small deviations — that is the compression working as configured, not a bug. For a bit-exact archive use `--distance 0` (still ~40% smaller than an uncompressed TIFF). Note that `--mode` (0–8) only decides *where* output files go; quality is `--distance` alone.

#### There is a floor at 0.05 (measured, v1.9.0)

**cjxl clamps every lossy distance at or below 0.05 to the same value.** On a real 16-bit photo, `--distance 0.005`, `0.01`, `0.02`, `0.03`, `0.04` and `0.05` all produced a **byte-identical** 20,188,082-byte file. The menu and the CLI accept anything from 0 to 15, so setting `0.02` looks like it buys you something — it does not. Either stay at `0.05`, or go to `--distance 0` for true lossless. Since v1.9.0 the encoder warns when you ask for a distance in the dead zone.

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

### What's new — v2.0.3 (current stable)

**Released 2026-08-23.** Bug fixes only. No command line and no file format changes: v2.0.2 commands keep working exactly as written. Three audit rounds (32–34) across all four scripts, verified against the real fixtures — the 16-bit Capture One exports and the RGB+IR film scans — not just the mocked suite.

#### The JXL → JPEG delete gates trusted the JXL's name

`checksums.md5` holds the original JPEG's hash keyed by the JXL's **filename**. A delete-skipped run compared that stored hash against the recovered JPEG on disk — and the JXL being deleted never entered the comparison. Replace `photo.jxl` with a different, same-named JXL (a re-export, a restore mix-up) and the old archive still matched, so the replacement was deleted having never been archived. This was documented as "provenance PROVEN"; it was proven for the name, not the bytes.

The gates now bind the file's **content**. The encoder stores the JXL's own MD5 beside the original's (a `<name>.jxl-md5` companion line; old databases keep working), and a delete run compares it against the JXL in front of it. Archives written before this release fall back to a real `djxl --reconstruct_jpeg` comparison. When neither proof can run, the source is kept. Verified end to end with the real tools: a swapped JXL is kept with a message saying exactly why, on both the new and the legacy path.

#### An RGB ICC was attached to grayscale output

On the `--to-srgb` / `--icc-profile` paths (transcoder and decoder), grayscale output received the **RGB** profile — which PNG rejects as a mismatched `iCCP` and which is equally wrong on a 1-component JPEG. Every film-scan IR page took this path. Grayscale images now keep their pixels and skip the profile. *(Round 32.)*

#### The rest

- **The wrapper's manifest collision scan compares its two entry families against each other again.** A round-31 performance change bucketed entries into two families and only compared within each — a mode-6 entry and an in-place entry aimed at the marker's output folder could race on the same outputs from two child processes. Cross-family containment now forces the full scan; disjoint libraries keep the fast path.
- **A failed move out of staging no longer deletes a good pre-existing destination.** The cleanup assumed "staging copy survived ⇒ destination is partial", which is wrong when the move failed before writing (a locked or read-only destination) or after the copy but before the unlink (a complete copy). The destination's identity is snapshotted before the move; only a provably-written, provably-incomplete result is removed. All three backends, kept identical by the parity test.
- **Re-encoding a lost page of a pre-v2.0.2 multi-page archive heals it again.** v2.0.2's group-id change made the id cover the page set, so the re-encoded page landed in a different group than its surviving siblings — the decoder saw two truncated groups and sent the user hunting for a page that is not missing. The encoder now adopts the siblings' legacy id when they prove it (unanimous, matching the old formula, all within the planned page set), and the decoder recognizes the mixed-version shape and advises a full re-encode instead.
- **The decoder treats a failed metadata copy as an error that blocks the delete.** exiftool's exit code was never checked: a failed copy silently dropped the metadata, the pixel-valid TIFF passed the integrity gate, and `--delete-source` removed the JXL — the only remaining copy of that metadata.
- **The decoder's multi-page marker reader normalizes path case**, like its sibling already did; an exiftool reply with a differently-cased drive letter silently dropped the group markers, and a delete run then peeled a split apart page by page.
- **The `--delete-source is ARMED` dry-run notice now prints in all three transcoder entry points** — v2.0.1's changelog claimed it did; only one of the three had it.
- Plus a per-script batch of lows: the wrapper charges the delete token only after checking the child script exists, `--list-presets` works without codecs, Ctrl+C cancels cleanly (exit 130, summary still printed), hand-written mode-7 manifests get their `--export-subfolder`, corrupt session files are refused field by field; the encoder counts skipped files in the real-run summary, validates `TEMP2_DIR`, classifies zero-page TIFFs as corrupt in every multipage mode, and no longer lets mode 6 honor the mode-7 subfolder exemption; the transcoder asks for the requested bit depth on the ICC paths and stops charging the lossy token for PNG → JXL at `--distance 0`.

**1129 tests**, up from 1027 — every fix-targeted test verified failing against the pre-fix code. Round trips re-verified on the real fixtures: TIFF → JXL → TIFF pixel-identical (16-bit ProPhoto exports; the RGB+IR scan, thumbnail excluded, IR page grayscale), JPEG ↔ JXL MD5-exact, and the swapped-JXL scenario above run end to end. Full detail in [bug tracking](docs/bug_tracking_since_v1.0.md) (rounds 32–34).

---

### v2.0.2 — previous stable

**Released 2026-08-19, superseded by v2.0.3 and kept here for reference.** Bug fixes only. No command line and no file format changes, and JXLs from earlier versions are read the same way.

Converting a file has been clean in every audit round. This one looked at converting it a **second** time, and that was not.

#### Re-archiving a scan merged a page from the previous round

A film scan is `[image, thumbnail, IR]`, and `--thumbnail-mode exclude` leaves the real pages on their original indices — so the archive is pages `{0, 2}`: `scan.jxl` and `scan_page2.jxl`. Decode that and the TIFF has two pages, `[image, IR]`. Encode it again in the same folder and the IR page is index 1, so you get `scan.jxl` and `scan_page1.jxl` while `scan_page2.jxl` from the first round stays where it was.

The multi-page group id was a hash of the source *path*, identical both times, so all three files claimed the same group and the decoder wrote them into one TIFF with the IR page twice — a structurally valid file, reported as `0 errors`.

Nothing was destroyed: the group was flagged as incomplete and `--delete-source` kept the sources. But the TIFF was wrong and the run called itself a success. Three changes close it — the group id now covers the pages a split produces, so a leftover cannot join a later one; the decoder refuses to merge a group holding more members than the split recorded, telling them apart by the source-bytes id each output carries and failing closed when it cannot; and the encoder names leftovers it finds in the destination. The second of those also repairs folders **already** in this state, where every member shares one id.

#### The rest

- A manifest that deletes now asks the same three questions as the `[D]` menu: verify each output against its source, whether to cover originals already converted, and how an existing output is matched to the source replacing it. A manifest does not go through that menu, so it reached the deletion with the structural check alone.
- "Incomplete group" gave one message and one piece of advice for two opposite problems — pages missing and pages extra.
- Mode-6 manifests no longer walk every library before starting. Entries writing inside their own Source cannot collide when the Sources are disjoint, so `G:\2024` / `G:\2025` / `G:\2026` begins converting immediately. Sources are now checked for overlap rather than assumed disjoint.
- `Added JPEG preview ... with ICC` was logged with no ICC attached — every film-scan IR page, where the profile is inherited and deliberately not written.
- `CreatorTool` went into an exiftool argfile unsanitised in the transcoder, which had no `_argfile_safe` at all.
- One of two readers split a single-value `dc:Relation` on commas, enough to tear `Smith, John` in half.
- A JXL → JPEG preset showed a `d=` it never uses, left over from an earlier TIFF run.
- The cautious ICC test held its lock across the probe, stopping every worker the first time a profile appeared.
- The `output` positional was documented "mode 0 only"; mode 2 takes it too.

**1017 tests**, up from 994 — eighteen of the twenty new ones verified failing against the pre-fix code. Full detail in [bug tracking](docs/bug_tracking_since_v1.0.md) (round 31).

---

### v2.0.1 — previous stable

**Released 2026-08-13, superseded by v2.0.2 and kept here for reference.** A maintenance release on top of v2.0.0. Nothing here changes a command line or a file format: v2.0.0 commands keep working exactly as written.

v2.0.0 shipped a lot of new machinery around the moment a file is deleted, so this round audited it the way the [AGENTS notes](AGENTS.md) ask for — against real photos rather than the synthetic test suite. The fixtures were 16-bit Capture One exports, a 260 MB Z8 export, and the 756 MB RGB+IR film scans (RGB page + embedded preview + IR `MASK` page, carrying a 217 KB scanner ICC).

**The conversion path came out clean, end to end.** Every lossless round trip was pixel-identical with the ICC, `SubfileType` and page structure intact — the IR `MASK` page included. JPEG ↔ JXL recovered byte-identical with MD5 PASS. Alpha, pure grayscale, 8-bit and CJK/accented paths all survived. The v2.0.0 gates held under real files too: the cross-run provenance refusal, the incomplete-split KEEP, staging + delete, and the scanner-ICC lossy workaround each did what the READMEs promise.

The six defects are all *around* that core:

- **The delete confirmation counted the wrong files in mode 7.** The wizard's "About to delete originals" panel applied the export marker but not the `--export-subfolder`, so it counted every subfolder under the marker for a run that converts one of them. With `_EXPORT` holding `16B_TIFF`/`AdobeRGB`/`sRGB` it announced 2 files (TIFF → JXL) or 3 (JXL → TIFF) for a run that touches 1 — and a *disjoint* set, not a superset. That count is the documented way a wrong folder is caught before the HHMM token is charged, in the mode the READMEs call the most common Capture One workflow. The run itself always converted the right files; only the preview lied.
- **A manifest run leaked its export marker into the rest of the menu session**, which made the count above depend on what had been run earlier. Both sites now share one helper so they cannot drift apart again.
- **`--multipage-mode split_all` reported `Thumbnail: exclude`** in the opening banner while encoding every thumbnail, two lines above its own log of a written `*_thumbnail.jxl`.
- **The decoder README still documented the `SubfileType=4` (MASK) downgrade** that v2.0.0 had already fixed — so someone reading it about their own film scans was told the IR page is demoted when it is not.
- **The dependency status bar was unreadable in a redirected log**: `✓` and `✗` both became `?`, so a scheduled-task log could not say which tool was missing.
- One comment in the integrity gate understated its cost by a whole page.

**994 tests**, up from 981. Ten of the thirteen new ones were verified failing against the pre-fix code; the other three are controls that must pass on both sides. Full detail in [bug tracking](docs/bug_tracking_since_v1.0.md) (round 30).

---

### v2.0.0 — previous stable

**Released 2026-08-09, superseded by v2.0.1 and kept here for reference.** Everything below landed after v1.9.1, across seven internal audit rounds. Ordinary conversion is unchanged — same pixels, same ICC, same metadata, validated again on real Capture One exports and the 756 MB RGB+IR film scans. What changed is everything around the moment a file is **deleted**.

#### Upgrading from v1.9.1

Two behaviour changes can affect a command line you already have. Both are in the destructive path; nothing else needs attention.

**1. `--delete-source` is honoured in every mode.** v1.9.1 had `if DELETE_SOURCE and mode == 8` in all three scripts — outside mode 8 the flag was accepted and ignored. If you have a saved command, preset, manifest or scheduled task using `--delete-source` with any other mode, it deleted nothing before and deletes the originals now.

*What to do:* re-read any stored command that carries `--delete-source`. If the intent was "keep both", drop the flag. If the intent was archival, it now works as written — try it on one folder with `--dry-run` first, which reports exactly which sources would go.

**2. Outputs written before v2.0.0 can be refused.** In the folder-collapsing modes — 2, 4, 5, 6, 7, and now mode 0 when given an output folder — two sources in different folders can resolve to the same output. A run that overwrites an existing output *and* deletes the source that produced it would destroy the earlier photo, whose own original is already gone. So every conversion now records which source made it, and a delete run refuses an output it cannot tie to the source in front of it. An archive built before this release carries no such record.

*What to do, by direction:*

| Direction | Migration |
|---|---|
| TIFF → JXL | `--provenance adopt` once. Each unrecorded output is decoded, compared against its source, and stamped — a one-time healing pass, after which the strict check applies again. `--no-adopt-scan` skips the verification if you would rather trust the pairing. |
| JXL → TIFF, JPEG ↔ JXL | No adopt yet. Use a structure-preserving mode (0/1/3/8) for those folders, where nothing can collide and nothing is refused. |

**Also worth knowing if you script the tools:** a provenance refusal is a failure — it counts into the error total and exits `1`, where a refused run used to exit `0`. And outputs now carry `jxlphoto-*` markers in `XMP-dc:Relation`; they are stripped from anything this toolkit reconstructs, so a round trip is unaffected.

#### Archive and replace

- **`--delete-source` works in every mode (0–8)**, in all three scripts. Convert into a separate tree and drop the originals — the workflow that was previously impossible without doing the move yourself.
- A source is deleted only when: every page of it converted **this run**; no page was dropped by the multi-page policy; the output exists at its **final** path (and if staging was used, the move there actually succeeded — a stale file already sitting there does not count); it passes the integrity check **there**; and, with `--verify-roundtrip`, it decodes back to the source pixels.
- **`--verify-roundtrip`** *(TIFF → JXL)* — the only gate that looks at pixels rather than at file structure. At `--distance 0` the decode must be identical; on a lossy run it is a brightness + PSNR sanity check that catches a black or scrambled encode. Opt-in: it costs one full decode per output.
- **`--delete-skipped`** — also delete sources whose output already exists, so an archive interrupted between the conversion and the unlink can be finished without re-encoding the library. Never acts on the timestamp: the output must exist and pass its checks.
- **Three gates before anything goes**, each more specific than the last: a plain y/N, then the concrete consequence (how many files, from which folder, to where), then a time token that cannot be answered by reflex. A dry run never charges the token.

#### Provenance

- Every conversion records **which source produced it** — `jxlphoto-src` (the location) and `jxlphoto-srcsum` (the bytes), in the XMP `dc:Relation` bag.
- `--provenance path` (default, free) compares the recorded location · `content` also accepts matching source bytes, so it survives folders you moved · `adopt` (**TIFF → JXL only**) verifies and stamps an archive built before the markers existed.
- A mismatch always fails closed: not converted, nothing overwritten, nothing deleted. `adopt` relaxes "I cannot tell", never "I can tell it is wrong".
- The check also covers **mode 0 with an output folder**, which flattens every source into that folder exactly like mode 2.

#### Multi-page and film scans

- A split now records **how many JXLs it produced** (`jxlphoto-pages`). A group that arrives with pages missing is reported and its sources kept — the short TIFF it produces is a perfectly valid file, so no integrity check, round trip or checksum downstream could tell it was incomplete.
- `--allow-incomplete-groups` deletes anyway, for a page that is genuinely lost.
- A gap in the page numbers is **not** treated as a missing page: `--thumbnail-mode exclude` drops the thumbnail and leaves the real pages on their original indices, so the ordinary `[real, thumb, real]` scan archives completely as pages `{0, 2}`.
- Scanner IR pages keep their role — `SubfileType=4` was being downgraded to 2 on decode.

#### Robustness

- **A failed move out of staging no longer leaves a truncated file** at the destination with a fresh timestamp, which smart-sync would then skip forever. A destination volume that is full stops the run instead of producing one failure line per remaining file.
- **`--dry-run` touches nothing.** It no longer creates the staging or temp folders while validating them, and no longer stamped provenance markers into real files.
- **Refusals reach the exit code and the summary** in all three scripts and all their commands — an auto-mode run that refused every file used to exit 0 with an empty failure list.
- **A rejected command line says so**: the wrapper tells a usage error apart from a safety abort instead of reporting both as "aborted by a safety check".
- A manifest stops when a child aborts, `--clean-staging` never sweeps during a dry run, and messages emitted before the logger was configured now reach the log.
- Flags that do nothing in the given combination say so instead of looking effective.

#### Under the hood

- 121 new regression tests (830 → 973), every one verified failing against the code before its fix.
- A parity test pins the helpers the four scripts deliberately duplicate, so a fix applied to one copy cannot silently miss the others.

### v1.9.1 — previous stable

**Released 2026-08-02, superseded by v2.0.0 and kept here for reference.** This is the release where the tool stops flying blind: a run now measures how much space it will need before it starts, stops cleanly instead of grinding when a disk fills, says something during a slow folder scan instead of looking frozen, and reports what it left behind in staging.

v1.9.1 itself is a small fix on top of v1.9.0: **manifest runs no longer stall silently before starting.** The cross-entry collision guard skips its full recursive scan for the per-source output modes (0/1/3/6/7/8), where every entry writes inside its own Source tree and a cross-entry collision is impossible — mode 6/7 manifests over large libraries now start immediately. When the scan does run (modes 2/4/5, where entries can share an output folder), each entry prints `Collision check: scanning <folder> ...` so the wait is visible. No changes to conversion logic; safe update for everyone.

> **Behaviour change in v1.9.0 — read this if you script the tools.** A run that fills its output volume now **aborts and exits 2** instead of failing every remaining file and exiting 1. Automation that treats "non-zero" as one bucket is unaffected; automation that distinguishes `1` (some files failed) from `2` (aborted) will now see `2` for a full disk — which is the retryable case.

Highlights (v1.9.0):

- **Space estimate before the run starts** *(TIFF → JXL only)* — `jxl_tiff_encoder.py` encodes three crops of your own files to measure this batch, then warns if the output will not fit staging or destination. Warns only, never blocks. `--no-preflight` skips it.
- **A full disk stops the run** — instead of failing every remaining file one by one. Queued files are reported as *not attempted*, and the run exits `2`.
- **Slow folder scans show progress** — a 25-second walk on an external drive no longer looks frozen. Fast local scans stay silent.
- **Staging leftovers are reported** — and `--clean-staging` sweeps the old ones.
- **Distances below 0.05 do nothing** — cjxl clamps them; `0.02` and `0.05` give byte-identical files. Use `--distance 0` for real lossless.
- **Three delete-gate bypasses closed** — `--delete-source` in the wrapper's expert-flags field could delete originals unattended.
- **Corrupt saved workflows are refused** — a hand-edited config no longer ends the run in a traceback.
- **Manifest entries with `..` refuse the whole file** — they used to be skipped while the run carried on.

Full release notes: [v1.9.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.9.1) · [v1.9.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.9.0) · [v1.8.4](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.4)

### v1.8.4 — previous stable

**Released 2026-07-28, superseded by v2.0.0 and kept here for reference.** Adds `--run-preset NAME`, which runs a saved preset without the menu and exits, so a recurring sync can live in Task Scheduler or cron. Sync is the default (`--overwrite` to redo everything) and `--dry-run` is never inherited from the stored run; presets that delete sources are refused unattended. `--list-presets` shows what is saved. Everything below came with v1.8.3 and is unchanged.

> **Breaking change (inherited from v1.8.0):** in `jxl_jpeg_transcoder.py`, modes **4 and 5 were swapped** — **4 = folder rename**, **5 = sibling folder**. Swap them in saved commands and manifests.

Highlights:

- **Your own distance in the menu** — entry `[2]` of Step 2 now shows the distance you set in option 4 (e.g. `d=0.05 — Your default`), instead of forcing a trip through `[4] Custom` on every single run. Ships as `0.1`, so nothing changes until you set it. `[4] Custom` comes pre-filled with the last value you used.
- **Manifest runs are repeatable** — `Repeat last workflow` now handles them: it re-reads the CSV (so edits you made in Excel count), skips the pointless input-folder question and goes straight to overwrite/sync and dry-run. Keeping a library in sync is two keystrokes; it used to mean walking through the whole wizard again.
- **Presets (option 7)** — save the last workflow under a name and run it later. Several recurring jobs (a nightly manifest sync, a per-shoot conversion) can coexist instead of overwriting one another in the single "last workflow" slot.
- **Settings that actually apply** — changing workers/quality/effort/distance in option 4 now also drives the next run, repeats included. They used to write a separate value that no run ever read, so editing them appeared to do nothing.
- **Manifest runs end with a real summary** — a per-folder table, a file-level TOTAL across every entry, and the **paths** of the files that failed. A multi-hour run over three folders used to end with `3 OK` (counting folders), the per-folder numbers already scrolled away into three separate logs. Also saved to `Logs/jxl_photo/<timestamp>.log`.
- **Corrupt files no longer hide in the `skipped` count** — a TIFF with no readable pages was reported as "skipped by multipage policy", a policy that never asked for it. It now has its own count and its own section. Exit codes are unchanged: a damaged input is not a failed run.
- **No silent data loss** — 16-bit decode failures, a "repaired" corrupt JXL, and weak delete verification now all fail loudly instead of quietly proceeding.
- **Multi-page TIFFs are safe by default** — every page is kept (`--multipage-mode split`) instead of just page 0, and mode 8 refuses to delete a source whose pages were dropped.
- **Every output is verified before being trusted** — a corrupt or partial JXL/JPEG/PNG/TIFF is caught and cleaned up instead of reported OK.
- **Manifests are Excel-safe and collision-checked** — UTF-8 BOM for non-ASCII paths, cross-entry output collisions refused up front.
- **Better throughput on large libraries** — the thread pool no longer stalls at folder boundaries.

Full release notes: [v1.8.4](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.4) · [v1.8.3](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.3) · [v1.8.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.2)

### Release history

| Version | Date | Highlights |
|---------|------|------------|
| **v2.0.3** | 2026-08-23 | Maintenance. The JXL → JPEG lossless delete gates trusted the JXL's **name**, not its bytes — a swapped same-named JXL could be deleted unarchived; the gates now bind content (own-MD5 + `reconstruct_jpeg` fallback, fail closed). An RGB ICC reached grayscale output (film-scan IR pages) on the `--to-srgb`/`--icc-profile` paths. A failed staging move could delete a good destination; a pre-v2.0.2 multi-page archive split in two when a lost page was re-encoded (it heals now). 32 fixes across rounds 32–34 |
| v2.0.2 | 2026-08-19 | Maintenance. Re-archiving a multi-page scan a **second** time left a page of the previous split behind, and the next decode merged it back in — a TIFF with a page repeated, reported as a clean run. The group id identified only the source, not the split; fixed on both sides, and the decoder now repairs archives already in that state. Plus: manifest deletions get the same gates as the `[D]` menu, mode-6 manifests skip a collision scan that cannot find anything, and seven smaller fixes |
| v2.0.1 | 2026-08-13 | Maintenance. v2.0.0's delete machinery audited against the real film scans and Capture One exports — the conversion path came out clean (every lossless round trip pixel-identical), and the six fixes are all around it: the mode-7 delete preview counted the wrong files, a manifest run leaked its export marker into the session, `split_all` mis-reported its thumbnail policy, and the dependency bar was unreadable in a redirected log |
| v2.0.0 | 2026-08-09 | Archive and replace: `--delete-source` in every mode, `--verify-roundtrip`, `--delete-skipped`. Provenance markers tie every output to the source that made it, so a delete run cannot overwrite one archive with an unrelated photo (**breaking**: pre-v2.0.0 archives are refused until adopted). Incomplete multi-page splits detected and their sources kept. Staging, dry-run and refusal-reporting hardening across all four scripts |
| v1.9.1 | 2026-08-02 | Manifest collision check skipped for the per-source output modes (0/1/3/6/7/8), where a cross-entry collision is impossible — mode 6/7 manifests over large libraries start immediately; a progress line when the scan does run (modes 2/4/5) |
| v1.9.0 | 2026-08-01 | Measured space estimate before a batch starts; a full output volume aborts the run (**exit 2**) instead of failing every remaining file; progress during slow folder scans; staging leftovers reported and sweepable (`--clean-staging`); distances ≤ 0.05 documented as identical; three delete-gate bypasses closed; corrupt saved workflows refused instead of crashing |
| v1.8.4 | 2026-07-28 | `--run-preset NAME` runs a saved preset unattended (Task Scheduler / cron): sync by default, dry-run never inherited, destructive presets refused |
| v1.8.3 | 2026-07-28 | Configurable default distance in the menu, repeatable manifest runs, named presets, settings that reach the next run; manifest run summary: per-folder table, file-level totals, failed paths listed; corrupt files split out of the `skipped` count; combined log in `Logs/jxl_photo/` |
| v1.8.2 | 2026-07-27 | Independent audit + real-batch fixes: ignored thumbnails no longer deleted, missing tools fail fast, multi-page default is now `split`, thread pool no longer stalls across folders |
| v1.8.1 | 2026-07-26 | Audit release: data-safety hardening, multi-page reconstruction v2, integrity gates, manifest coverage guards |
| v1.8.0 | 2026-07-18 | libjxl v0.12 support, output integrity verification, direction-restriction flags, transcoder modes 4/5 swapped |
| v1.7.2 | 2026-07-18 | Wrapper delete-source confirmation unstuck; lossy convert keeps Exif/XMP before the codestream |
| v1.7.1 | 2026-07-13 | Cautious ICC strategy (round-trip test + cache), `.jfif`/`.jpe` support |
| v1.7.0 | 2026-07-12 | Multi-page TIFF support: split/skip/ignore, thumbnail handling, per-page ICC, marker-based reconstruction |
| v1.6.0 | 2026-07-05 | Audit-driven fixes: staging concurrency, wrapper routing, manifest Mode column, CMYK rejection |
| v1.5.3 | 2026-04-15 | Full Auto Mode, PNG bit depth, EXIF preservation, 8-bit TIFF black-image fix, stable |
| v1.4 | 2026-04-11 | JXL → JPEG workflow: lossy/lossless conversion modes |
| v1.3 | 2026-04-11 | Auto Mode (beta), manifest system, embedded JPEG thumbnail |
| v1.2 | 2026-04-05 | Basic/None decode modes, ICC mode selector |
| v1.1 | 2026-04-05 | D50 patch modes, metadata strip, race-condition fixes |
| v1.0 | 2026-04-02 | First stable release — TIFF and JPEG → JXL with ICC preservation |

### Older history

- Release notes before v1.8.2: [v1.8.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.1) · [v1.8.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.8.0) · [v1.7.2](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.2) · [v1.7.1](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.1) · [v1.7.0](https://github.com/rsilvabr/jxl-photo/releases/tag/v1.7.0)
- [Version history](docs/version_history.md) — "What's New" notes for releases before v1.8
- [Bug Tracking (v1.0 → current)](docs/bug_tracking_since_v1.0.md) — bugs fixed since v1.0
- [New Features (v1.0 → current)](docs/new_features_since_v1.0.md) — genuinely new features
- [Code Quality & Refactoring](docs/code_quality_refactoring.md) — internal cleanups, compatibility backports, dead code

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

## Related project: a simpler, TIFF-only alternative

If this toolkit's setup is more than you want to deal with, [convert_tiff_to_deflate](https://github.com/rsilvabr/convert_tiff_to_deflate) is a standalone PowerShell script that compresses TIFFs with ZIP/Deflate compression.

- **What you need:** PowerShell 7 (or Windows PowerShell 5.1), ImageMagick, ExifTool
- **What's NOT needed:** Python or Python packages, libjxl (cjxl/djxl)

| Format | 16-bit Size | 8-bit Size |
|--------|-------------|------------|
| Uncompressed TIFF | ~260 MB | ~130 MB |
| **ZIP/Deflate (PowerShell)** | ~220 MB (~15% smaller) | ~65 MB (~50% smaller) |
| JXL lossless (this toolkit) | ~173 MB (~35% smaller) | ~43 MB (~67% smaller) |
| **JXL lossy d=0.1 (this toolkit)** | ~34 MB (~87% smaller) | ~8 MB (~94% smaller) |

**Trade-off:** easier to install, but much less compression, and the output stays a TIFF. Still better than nothing. Once you are comfortable with ImageMagick and ExifTool, the setup here is the same two tools plus Python and libjxl.

---

## License

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation  
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling  
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O  
- [Kimi](https://www.kimi.com) (Moonshot AI) and [Claude](https://www.anthropic.com/claude) (Anthropic) for code assistance and technical discussion
