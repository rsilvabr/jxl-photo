# jxl_photo — JXL Workflow Manager

Batch JPEG XL conversion tools with **full ICC color profile and EXIF metadata preservation**. Designed for photographers working with 16-bit TIFF files who want compact JXL archives without losing color accuracy or metadata. Tested with Capture One, Lightroom, NX Studio, Photoshop, and Fuji Hyper Utility exported 16-bit TIFFs.

**Current version: v2.9.0** (2026-10-10) · [What's new](#whats-new-in-v290) · [Upgrading from an older version](docs/upgrading.md) · [All releases](docs/version_history.md)

---

## Contents

- [Why JPEG XL?](#why-jpeg-xl)
- [Features](#features)
- [Scripts](#scripts)
- [Requirements & Installation](#requirements--installation)
- [Quick Start — Interactive Wrapper](#quick-start--interactive-wrapper)
- [Auto Mode and manifests](#auto-mode-and-manifests)
- [Individual Scripts](#individual-scripts)
- [Recommended Settings](#recommended-settings)
- [How the ICC profile is preserved](#how-the-icc-profile-is-preserved)
- [Good to know](#good-to-know)
- [Documentation](#documentation)
- [What's new in v2.9.0](#whats-new-in-v290)
- [More about this project](#more-about-this-project)
- [Related project: a simpler, TIFF-only alternative](#related-project-a-simpler-tiff-only-alternative)
- [Disclaimer](#disclaimer) · [License](#license) · [Acknowledgments](#acknowledgments)

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

**TIFF → JXL** — 16-bit lossless or near-lossless archives. The exact original ICC profile comes back on decode, even from a lossy file; EXIF/XMP stay visible in IrfanView, XnView MP and other viewers. Multi-page TIFFs and film scans (IR page included) are split and rebuilt page for page.

**JXL → TIFF** — Roundtrip, Basic and Matrix decode modes, a JPEG preview for fast Explorer thumbnails, and a sync that reconverts only what changed.

**JPEG ↔ JXL, JXL → JPEG/PNG** — lossless JPEG → JXL with the bit-exact JPEG back on demand; JPEG/PNG delivery copies with colour conversion (sRGB, AdobeRGB or any profile), resize and output sharpening fitted to Capture One's presets.

**JXL → JXL** — shrink an archive to a new distance in one step, with counterproductive re-encodes refused and metadata carried over; light 16-bit derivatives (sRGB, resized, sharpened) made from the master.

**Batch work** — folder modes for flat, recursive and Capture One / Lightroom export layouts, parallel workers, SSD staging, folder exclusions; [manifests](docs/README_manifest.md) (one CSV, many folders, checked before anything runs) and presets that run unattended from Task Scheduler ([setup](docs/README_jxl_tools.md#running-a-preset-unattended-task-scheduler--cron)).

**Archive and replace, safely** — `--delete-source` works in every mode, but a source is deleted only after its output is written, checked, and (optionally) decoded back to the same pixels. Every output records which source made it, so a later run never mistakes one photo's archive for another's, and three confirmations stand before any deletion.

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

### 1. Python 3.9+ and packages

```powershell
pip install tifffile numpy pillow rich imagecodecs
```

Install them in the same Python you will run the scripts with.

### 2. External tools (download the executables, not the source code)

| Tool | Download URL | What to download | Extract to |
|------|-------------|------------------|------------|
| **cjxl / djxl** | https://github.com/libjxl/libjxl/releases | `jxl-x64-windows-static.zip` (**not** `jxl-x64-windows.zip`, which has only DLLs) | `C:\tools\libjxl\` or your choice |
| **exiftool** | https://exiftool.org | `exiftool-XX.XX_64.zip` (**not** the `.tar.gz`, which is Perl source) | `C:\tools\exiftool\` or your choice |
| **ImageMagick** | https://imagemagick.org | Installer `.exe` (Q16-HDRI x64) | Default location |

The scripts find exiftool under either name, `exiftool.exe` or the download's `exiftool(-k).exe`.

Tested with libjxl 0.12.0 (0.11.2 still works), exiftool 13.59, ImageMagick 7.1.2-27, numpy 2.5.1, tifffile 2026.6.1, Pillow 12.3.0, imagecodecs 2026.6.26 and rich 15.0.0. The scripts detect the libjxl version and adapt. Memory per worker is about 35–40 MB per megapixel (1.55 GB for a 45 MP file); see [RAM per worker](docs/README_jxl_tiff_encoder.md#ram-per-worker) before raising `--workers`.

### 3. Add to PATH (PowerShell)

**Replace the example paths below with YOUR actual installation paths:**

```powershell
# EDIT THESE PATHS to match where YOU extracted the tools:
$myPaths = @(
    "C:\tools\libjxl\bin",                           # where cjxl.exe and djxl.exe are
    "C:\tools\exiftool",                              # where exiftool.exe is
    "C:\Program Files\ImageMagick-7.1.1-Q16-HDRI"     # where magick.exe is
)

# Add to user PATH
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", ($myPaths -join ";") + ";$p", "User")

# RESTART your PowerShell/terminal after this!
```

### 4. Verify

Restart PowerShell, then:

```powershell
cjxl --version          # cjxl v0.XX.X
exiftool -ver           # 12.XX or 13.XX
magick -version         # ImageMagick version
python -c "import tifffile, PIL, rich; print('All Python packages OK')"
py jxl_photo.py         # from the repository folder
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

Setup feels heavy? [tiff-workflow](#related-project-a-simpler-tiff-only-alternative) needs no libjxl at all.

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

The value between `[brackets]` (blue in the menu) is the default: press `Enter` to accept it. Full wrapper guide: [docs/README_jxl_tools.md](docs/README_jxl_tools.md). Its settings live in `.jxl_tools_config.json`, next to `jxl_photo.py` or in your home folder (main menu option 6 moves it).

---

## Auto Mode and manifests

Instead of memorizing modes 0–8, press **[A]** in Step 4: the wizard reads your folder structure and *recommends* a mode — `_EXPORT` / `Export_*` folders → 6/7 (Capture One / Lightroom), deep subfolders → 3, several folders → 2, a single folder → 0 — and previews where each folder's output goes. It never runs anything you have not confirmed; **[N]** picks the mode yourself.

From there, **[P]** writes a **manifest**: a CSV with one Source → Destination row per folder, each with its own mode (and optionally its own colour space, size, sharpening, rename or export folder). Edit it in Excel, comment a row out with `#`, and run it again any time with **[M]**:

```csv
Source,Destination,Mode,Direction
F:\2025\Recife\_Export\TIFF,F:\2025\Recife\_Export\TIFF,6,tiff2jxl
F:\2025\São Paulo\_EXPORT\16bit,F:\2025\São Paulo\_EXPORT\16B_JXL,7,tiff2jxl
# F:\2025\横浜\RAW,F:\2025\横浜\JXL,0,tiff2jxl
```

The whole manifest is checked before anything runs: two rows writing the same file, or an invalid cell, refuse it up front. Every column, what can be left out, and recipes: [docs/README_manifest.md](docs/README_manifest.md).

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

# Simulate first — nothing written, copied or deleted
py jxl_recompressor.py "F:\Photos\Archive" --mode 1 --distance 1.0 --dry-run

# Replace the archive in place, deleting nothing until verified
py jxl_recompressor.py "F:\Photos\Archive" --mode 8 --distance 1.0 --verify-roundtrip

# Light 16-bit sRGB derivatives of the Capture One masters, profile swapped in the name
#   _EXPORT/16B_JXL/_DSC0013_ProPhoto-g22_v1.jxl -> _EXPORT/16B_JXL_sRGB/_DSC0013_sRGB_v1.jxl
py jxl_recompressor.py "F:\Photos\2025" --mode 7 --export-subfolder 16B_JXL --export-jxl-folder 16B_JXL_sRGB --output-icc sRGB --distance 1.0 --sync --rename-from ProPhoto-g22 --rename-to sRGB
```

A derivative is built from the master JXL, not the TIFF — measured on a real ProPhoto export, the extra generation costs 0.017 dB against a direct encode of the TIFF, at the same size. See [Colour-converted derivatives](docs/README_jxl_recompressor.md#derivatives-colour-conversion-resize-and-sharpening).

Every option of every script: the per-script READMEs under [Documentation](#documentation).

### After conversion

1. Keep both TIFF and JXL — exclude the TIFF export folders from backups to save space (FreeFileSync folder filters make this easy).
2. Delete the TIFFs, keep only the JXLs — with [delete-tiff-exports](https://github.com/rsilvabr/delete-tiff-exports), or with this toolkit's `--delete-source`.

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

## How the ICC profile is preserved

```
Plain cjxl/djxl:   TIFF (ProPhoto, Kodak TRC) → JXL (native primaries) → TIFF with a generic profile
With this toolkit: TIFF (ProPhoto, Kodak TRC) → JXL (+ the original ICC in XMP) → TIFF with the ORIGINAL profile
```

The encoder stores the original ICC profile, base64-encoded, in the JXL's XMP (`xmp:CreatorTool`), and the decoder's Roundtrip mode puts it back byte for byte. `dc:Description` carries the encode record (`gen=1 | cjxl d=0.1 e=7`), which the recompressor reads. To check a round trip:

```powershell
exiftool -ProfileDescription -ProfileCopyright original.tif roundtrip.tif   # should match
```

XYB, ICC blobs and the table-curve profiles that need special handling: [docs/jxl_color_internals.md](docs/jxl_color_internals.md).

---

## Good to know

One line each; the detail is in [docs/behavior_and_limitations.md](docs/behavior_and_limitations.md).

- **Lossy is the default** (`--distance 0.1`, near-lossless, about a tenth of the TIFF); use `--distance 0` for a bit-exact archive. [More](docs/behavior_and_limitations.md#lossy-is-the-default---distance-01)
- **cjxl 0.12 treats every distance up to 0.05 as 0.05**, so smaller values buy nothing there. [More](docs/behavior_and_limitations.md#the-distance-floor-depends-on-your-cjxl-005-on-libjxl-012-001-on-011)
- **Every page of a multi-page TIFF is converted by default**; embedded previews are dropped unless you ask for them. [More](docs/behavior_and_limitations.md#multi-page-tiffs-every-page-is-converted-by-default)
- **The decoder never overwrites an original TIFF**, nor a decode you edited since. [More](docs/behavior_and_limitations.md#the-decoder-never-overwrites-an-original-tiff)
- **Re-runs differ per script:** the TIFF encoder/decoder sync by date, the JPEG transcoder skips existing outputs. [More](docs/behavior_and_limitations.md#default-re-run-behavior-differs-per-script)
- **Film scans:** the IR page is kept, but scanner software may not recognize it for dust removal after the round trip — test one file first. [More](docs/behavior_and_limitations.md#film-scanners-ir-channel--digital-ice)
- **Viewers:** Windows Explorer thumbnails are not colour-managed, IrfanView hides EXIF of JPEG → JXL files; the files themselves are fine. [More](docs/behavior_and_limitations.md#viewer-quirks-not-data-loss)

---

## Documentation

| Document | Contents |
|----------|----------|
| [docs/README_jxl_tools.md](docs/README_jxl_tools.md) | The interactive wrapper |
| [docs/README_manifest.md](docs/README_manifest.md) | Manifests: every column, what can be omitted, recipes, and manifest → preset → Task Scheduler |
| [docs/README_jxl_tiff_encoder.md](docs/README_jxl_tiff_encoder.md) | TIFF → JXL encoding |
| [docs/README_jxl_tiff_decoder.md](docs/README_jxl_tiff_decoder.md) | JXL → TIFF decoding |
| [docs/README_jxl_jpeg_transcoder.md](docs/README_jxl_jpeg_transcoder.md) | JPEG ↔ JXL / JXL → PNG |
| [docs/README_jxl_recompressor.md](docs/README_jxl_recompressor.md) | JXL → JXL recompression |
| [docs/behavior_and_limitations.md](docs/behavior_and_limitations.md) | Defaults, limits and viewer quirks worth knowing before a large batch |
| [docs/jxl_color_internals.md](docs/jxl_color_internals.md) | Deep dive: XYB, ICC blobs vs primaries, troubleshooting |
| [docs/upgrading.md](docs/upgrading.md) | What to check when you upgrade from an older version |
| [docs/version_history.md](docs/version_history.md) | Every release in detail, and the full release table |
| [docs/bug_tracking_since_v1.0.md](docs/bug_tracking_since_v1.0.md) | Every bug fix since v1.0, numbered and dated |
| [docs/new_features_since_v1.0.md](docs/new_features_since_v1.0.md) | Every new feature since v1.0 |

**Testing.** `pytest tests/` runs the test suite (synthetic files, plus real-codec tests that skip when `cjxl`/`djxl`/`exiftool` are missing). [tools/real_photo_battery.py](tools/real_photo_battery.py) runs the four scripts end to end on **copies of your own photos** (`py tools/real_photo_battery.py --fixtures <folder>`; the fixture layout is in its docstring).

---

## What's new in v2.9.0

Released 2026-10-10. Faster recompressor runs on the big-memory settings. Nothing to do when upgrading.

- **The recompressor keeps its encodes busy.** At effort 8–9 (or effort 7 from distance 3) each cjxl needs a lot of memory, so the memory cap allowed only a few workers, and each one also spent time decoding and converting while its encode share sat idle. Now the cap limits the encodes themselves, and the memory left over runs extra workers that prepare the next files. A 45 MP run at d=3 e=9 goes from 3 workers to 3 encodes + 3 workers preparing. The log says `workers W, cjxl at a time S`.
- `WORKER_MEMORY_FRACTION` in the recompressor is now 1.0 (was 0.8); the TIFF encoder keeps 0.8.

Full notes: [version history](docs/version_history.md#v290) · [bug tracker](docs/bug_tracking_since_v1.0.md) (#529) · [Memory and --workers](docs/README_jxl_recompressor.md#memory-and---workers). **2311 tests**, and the real-photo battery passes (42 checks).

### Recent releases

| Version | Date | Highlights |
|---------|------|------------|
| **v2.9.0** | 2026-10-10 | The recompressor keeps its encodes busy: extra workers prepare the next files |
| [v2.8.2](docs/version_history.md#v282) | 2026-10-09 | The wizard's defaults follow the settings at the top of each script |
| [v2.8.1](docs/version_history.md#v281) | 2026-10-08 | The rest of the 2026-10-08 audit: a scan's IR channel keeps its role, an edited decode is left alone; smaller hardening |
| [v2.8.0](docs/version_history.md#v280) | 2026-10-08 | Overwrites check whose output they replace; scans keep their colours when recompressed; more delete-gate edge cases closed; workers capped by physical RAM too |
| [v2.7.0](docs/version_history.md#v270) | 2026-10-06 | Recompressor derivatives re-derived when distance/effort change; per-file exiftool calls get the codec timeout |

Every release since v1.0: [docs/version_history.md](docs/version_history.md#release-history).

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
| **JXL lossy d=0.1 (this toolkit)** | ~34 MB (~87% smaller) | ~34 MB (~74% smaller) — no smaller than 16-bit: [lossy JXL gains nothing from 8 bits](https://www.reddit.com/r/jpegxl/comments/1sp9qbj/analysis_jxl_distance_and_snr_16bit_vs_8bit_jpeg/) |

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
- [Claude](https://www.anthropic.com/claude) (Anthropic) and [DeepSeek](https://www.deepseek.com), among other AI tools, for code assistance, reviews and technical discussion
