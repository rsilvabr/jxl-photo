# jxl_photo — JXL Workflow Manager

Batch JPEG XL conversion tools with **full ICC color profile and EXIF metadata preservation**. Designed for photographers working with 16-bit TIFF files who want compact JXL archives without losing color accuracy or metadata. Tested with Capture One, Lightroom, NX Studio, Photoshop, and Fuji Hyper Utility exported 16-bit TIFFs.

**Current version: v2.27.0** (2026-11-11) · [What's new](#whats-new-in-v2270) · [Upgrading from an older version](docs/upgrading.md) · [All releases](docs/version_history.md)

**Windows program, no Python needed:** [jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe/releases/latest) — unzip and double-click; cjxl/djxl, ExifTool, ImageMagick and jxl-thumb included.

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
- [What's new in v2.27.0](#whats-new-in-v2270)
- [More about this project](#more-about-this-project)
- [Related project: jxl-photo as a Windows program](#related-project-jxl-photo-as-a-windows-program)
- [Related project: fast JXL thumbnails in Explorer](#related-project-fast-jxl-thumbnails-in-explorer)
- [Related project: a simpler, TIFF-only alternative](#related-project-a-simpler-tiff-only-alternative)
- [Related project: selective backup (photo-backup)](#related-project-selective-backup-photo-backup)
- [Disclaimer](#disclaimer) · [License](#license) · [Acknowledgments](#acknowledgments)

---

## Why JPEG XL?

Spectacular compression with no compromise on bit depth.

- Lossless 16-bit files much smaller than TIFF and TIFF with ZIP/Deflate
- Lossy 16-bit files — small files that retain full 16-bit tonal information, something no other common format achieves (JPEG is 8-bit, TIFF lossless is large)
- This is genuinely new: small lossy files, but with 16-bit color depth

Typical sizes in MB of ProPhoto exports from Capture One (median of 10–20 photos per resolution, ISO 64–4000). ZIP is the TIFF compression tiff-workflow writes. Detail and noise move the size more than the camera does — [every distance, with the spread across the photos](docs/file_sizes.md#every-distance-from-a-16-bit-and-an-8-bit-tiff):

| Format | 45 MP (Z7, Z8) | 24 MP (Zf) | 12 MP (D700) |
|--------|:--------------:|:----------:|:------------:|
| TIFF 16-bit / with ZIP | 273 / 227 | 146 / 120 | 72 / 60 |
| TIFF 8-bit / with ZIP | 136 / 61 | 73 / 30 | 36 / 16 |
| JXL lossless, from 16-bit | 173 | 89 | 43 |
| JXL lossless, from 8-bit | 43 | 21 | 10 |
| JXL `d=0.05` (finest lossy) | 42 | 21 | 9.9 |
| JXL `d=0.1` (near-lossless) | 31 | 15 | 7.2 |
| JXL `d=0.5` (high quality) | 11 | 4.9 | 2.4 |
| JXL `d=1.0` (visually lossless) | 5.5 | 2.7 | 1.2 |
| JXL `d=3.0` (web) | 1.8 | 0.9 | 0.4 |

`d=0.1` is the default and the recommendation for masters. Two tests of the grain ([details](docs/file_sizes.md#grain-after-heavy-editing-and-as-it-is)):

- **After heavy editing.** The worst case: the unlit black background of a flash photo — nothing there but grain (little noise reduction) — decoded back to TIFF and pulled up hard in Capture One (shadows +100, blacks +100, exposure unchanged). In that lifted black, at 100%, `d=0.1` matched lossless; at 300–800% its grain differed slightly, and only when flipping between the two files. `d=0.05` closes even that gap for about a third more space (42 against 31 MB at 45 MP, still a quarter of lossless); I use it for my own masters, out of caution. `d=0.5` softened that grain a little at 100% and `d=1.0` showed artifacts, while the lit subject looked clean even at `d=1.0`.
- **Without editing**, the photo as it is: `d=0.7` keeps the grain at 200%, where `d=1.0` starts to smooth it — a light file for copies you only look at.

Lossy sizes are about the same from an 8-bit and a 16-bit TIFF; only lossless differs. From an 8-bit TIFF, lossless is about the size of `d=0.05`, so jxl-photo archives 8-bit TIFFs lossless at `d=0.1` and below — see [Features](#features).

Film scans are archived lossless by default; that took 40–70% of the uncompressed TIFF on most scans tested (a 91 MP SilverFast negative with IR: 739 MB → 447 MB).

Effort 7 is the default: on files this size effort 9 gives the same files from `d=0.05` to `d=0.5`, slightly *larger* lossless ones, and only a small gain at `d=1.0`, for up to 16× the time. Per-photo numbers, scans and the quality measurements: [docs/file_sizes.md](docs/file_sizes.md).

The numbers behind these settings are measured and posted on r/jpegxl, on 16-bit ProPhoto exports from Capture One:

- [Distance, error and SNR](https://www.reddit.com/r/jpegxl/comments/1s6k718/analysis_jxl_distance_error_and_snr_analysis/): per-pixel SNR from `d=0.01` to `d=10` on a 45 MP photo. `d=1.0` is the sweet spot for pixels above 30 dB, `d=0.3–0.5` for 40 dB, and `d=0.05` for 50 dB: over 90% of the pixels stay above 50 dB, and not one pixel falls below 20 dB. That is where the archival setting comes from; the stress test above is from the same post.
- [16-bit vs 8-bit, and JPEG](https://www.reddit.com/r/jpegxl/comments/1sp9qbj/analysis_jxl_distance_and_snr_16bit_vs_8bit_jpeg/): for lossy JXL an 8-bit file is **not** smaller than a 16-bit one, and it keeps less signal, so there is no reason to go 8-bit. JPEG needs 5–15× the file size to get close to JXL.
- [Generation loss](https://www.reddit.com/r/jpegxl/comments/1wlmfw0/my_jxl_archive_tool_refuses_bad_recompressions_i/): one recompression from an archived `d=0.1` down to `d=1.0` costs 0.02 dB, but reaching the same file size in many small steps costs up to 9 dB. That is why the recompressor goes to the target in one shot, counts generations, and refuses counterproductive re-encodes.

The per-pixel SNR analyzer used in the first two is [jxl-quality-analyzer](https://github.com/rsilvabr/jxl-quality-analyzer); the harness, raw results and figures of the third are in [jxl-generation-loss](https://github.com/rsilvabr/jxl-generation-loss).

---

## Features

**TIFF → JXL** — 16-bit lossless or near-lossless archives. The exact original ICC profile comes back on decode, even from a lossy file; EXIF/XMP stay visible in IrfanView, XnView MP and other viewers. Multi-page TIFFs and film scans (IR page included) are split and rebuilt page for page.

**JXL → TIFF** — Roundtrip, Basic and Matrix decode modes, a JPEG preview for fast Explorer thumbnails, and a sync that reconverts only what changed.

**JPEG ↔ JXL, JXL → JPEG/PNG** — lossless JPEG → JXL with the bit-exact JPEG back on demand, its EXIF visible in IrfanView too; JPEG/PNG delivery copies with colour conversion (sRGB, AdobeRGB or any profile), resize and output sharpening fitted to Capture One's presets.

**JXL → JXL** — shrink an archive to a new distance in one step, with counterproductive re-encodes refused and metadata carried over; light 16-bit derivatives (sRGB, resized, sharpened) made from the master.

**The right distance for each file** — in one run, each TIFF gets the distance that suits it: an 8-bit TIFF is archived lossless at low distances, where lossy would come out *larger* (43 MB lossless against 47 MB at `d=0.05` for 45 MP); a film scan is archived lossless, since it can only be redone by scanning again. The run says which files got what, and each one's record names its distance ([8-bit](docs/README_jxl_tiff_encoder.md#8-bit-tiffs-are-archived-lossless-at-low-distances-eight_bit_distance), [scans](docs/README_jxl_tiff_encoder.md#film-scans-are-archived-lossless-scan_distance)).

**Viewer compatibility** — every JXL keeps its EXIF where IrfanView reads it: in a plain box, before the XMP and the image data. cjxl compresses it by default, which saves a median 2.3 KB per photo (0.19 %) and hides the EXIF from IrfanView; one setting turns that compression on if you prefer the smallest file ([details](docs/behavior_and_limitations.md#viewer-quirks-not-data-loss)).

**Fast thumbnails in Explorer, in the right colours** — Microsoft's JPEG XL extension decodes the whole image for every thumbnail and ignores the colour profile (ProPhoto and Adobe RGB look washed out). [jxl-thumb](https://github.com/rsilvabr/jxl-thumb), which I also made, fixes both for any lossy JXL: about 40× faster, with each file's own colour profile applied. On jxl-photo's outputs it's faster still — about 2 ms, like a JPEG, lossless included ([details](docs/behavior_and_limitations.md#viewer-quirks-not-data-loss)).

**Batch work** — folder modes for flat, recursive and Capture One / Lightroom export layouts, parallel workers, SSD staging, folder exclusions; [manifests](docs/README_manifest.md) (one CSV, many folders, checked before anything runs) and presets that run unattended from Task Scheduler ([setup](docs/README_jxl_tools.md#running-a-preset-unattended-task-scheduler--cron)).

**Archive and replace, safely** — `--delete-source` works in every mode, but a source is deleted only after its output is written, checked, and decoded back to the same pixels (by default for TIFFs, optional elsewhere). Every output records which source made it, so a later run never mistakes one photo's archive for another's, and three confirmations stand before any deletion.

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

> Rather not install Python and the tools? A Windows program packages
> everything: see [jxl-photo-exe](#related-project-jxl-photo-as-a-windows-program).

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

`jxlinfo.exe` comes in the same libjxl zip, next to `cjxl.exe` and `djxl.exe`: keep the three together. The scripts ask it whether a JXL holds float or animated content (and came from a rotated JPEG); without it they warn once, skip the float/animation check and read the orientation from the JXL's header themselves.

Tested with libjxl 0.12.0 (0.11.2 still works), exiftool 13.59, ImageMagick 7.1.2-27, numpy 2.5.1, tifffile 2026.6.1, Pillow 12.3.0, imagecodecs 2026.6.26 and rich 15.0.0. The scripts detect the libjxl version and adapt. A high `--workers` is safe: the encoder and the recompressor run only as many cjxl at once as the free memory holds ([how much each takes](docs/README_jxl_tiff_encoder.md#ram-per-worker)).

### 3. Add to PATH (PowerShell)

**Replace the example paths below with YOUR actual installation paths:**

```powershell
# EDIT THESE PATHS to match where YOU extracted the tools:
$myPaths = @(
    "C:\tools\libjxl\bin",                           # where cjxl.exe, djxl.exe and jxlinfo.exe are
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
jxlinfo -h              # prints its usage (it comes in the same zip)
exiftool -ver           # 12.XX or 13.XX
magick -version         # ImageMagick version
python -c "import tifffile, numpy, PIL, rich, imagecodecs; print('All Python packages OK')"
py jxl_photo.py         # from the repository folder
```

You should see: `[✓] cjxl/djxl | [✓] exiftool | [✓] magick | [✓] tifffile | [✓] pillow | [✓] imagecodecs | [✓] rich`

### Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| `cjxl` not recognized | Downloaded `jxl-x64-windows.zip` (runtime DLLs only) | Download `jxl-x64-windows-static.zip` |
| `exiftool` not recognized at the prompt | Its folder is not on PATH, or the file is still named `exiftool(-k).exe` — the scripts accept that name, your shell does not | Add the folder to PATH (step 3) and reopen the terminal; rename or copy to `exiftool.exe` if you want the bare command to work too |
| `exiftool` returns nothing | Downloaded `.tar.gz` (Perl source) | Download `.zip` with `_64` suffix |
| `ModuleNotFoundError` | Packages in different Python version | Run `python -m pip install tifffile numpy pillow rich imagecodecs` with the Python you run the scripts with |
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
│     CONVERT                                                                                                        │
│  1  New job: archive, restore, export, recompress...                                                               │
│  2  Repeat last job                                                                                                │
│  3  Presets (2 saved)                                                                                              │
│  4  Folder list (manifest): run a CSV of folders                                                                   │
│     MAINTAIN                                                                                                       │
│  5  Renamed or moved folders: fix the masters' path records                                                        │
│  6  Explorer thumbnails for existing JXLs (jxl-thumb)                                                              │
│  7  JPEG archives (jbrd): audit, repair, EXIF for IrfanView                                                        │
│  8  Reset the encoder's ICC test cache                                                                             │
│     SETTINGS                                                                                                       │
│  9  Settings: wrapper defaults, script settings, reset, move                                                       │
│  D  Dependencies and new releases: check again                                                                     │
│  0  Exit                                                                                                           │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

A **New job** asks what to do, where, and how, then shows one review screen:

```
[1] New job
  Task      → Archive TIFFs as JXL masters
  Folder    → F:\Photos\2024
  Mode      → 7 (below _EXPORT, one subfolder) — or [A] to have the folder analysed
  Quality   → d=0.1, near-lossless (recommended); film scans stay lossless
  Review    → every option with its value and where it came from;
              [a] all options, [d] dry run, [r] run, [s] save as a preset
```

The value between `[brackets]` (blue in the menu) is the default: press `Enter` to accept it. Full wrapper guide: [docs/README_jxl_tools.md](docs/README_jxl_tools.md). Its settings live in `.jxl_tools_config.json`, next to `jxl_photo.py` or in your home folder (Settings, menu 9, moves it).

---

## Auto Mode and manifests

Instead of memorizing modes 0–8, press **[A]** in the mode step of a New job: the wizard reads your folder structure and *recommends* a mode — `_EXPORT` / `Export_*` folders → 6/7 (Capture One / Lightroom), deep subfolders → 3, several folders → 2, a single folder → 0 — and previews where each folder's output goes. It never runs anything you have not confirmed; **[N]** picks the mode yourself.

From there, **[P]** writes a **manifest**: a CSV with one Source → Destination row per folder, each with its own mode (and optionally its own colour space, size, sharpening, rename or export folder). Edit it in Excel, comment a row out with `#`, and run it again any time with **[M]**:

```csv
Source,Destination,Mode,Direction
F:\2025\Recife\_Export\TIFF,F:\2025\Recife\_Export\TIFF,6,tiff2jxl
F:\2025\São Paulo\_EXPORT\16bit,F:\2025\São Paulo\_EXPORT\16B_JXL,7,tiff2jxl
F:\2025\横浜\RAW,F:\2025\横浜\JXL,0,tiff2jxl
F:\2026\京都・嵐山 [FINAL]\_EXPORT\TIFF,F:\2026\京都・嵐山 [FINAL]\_EXPORT\TIFF,6,tiff2jxl
# F:\2026\Outtakes (later)\_EXPORT\TIFF,F:\2026\Outtakes (later)\_EXPORT\TIFF,6,tiff2jxl
```

A row can also carry its own colour space, resize, sharpening and output folder — the JPEGs for the phone, made from the masters:

```csv
Source,Destination,Mode,Direction,OutputICC,Resize,Sharpen,RenameFrom,RenameTo,ExportSubfolder,ExportJxlFolder
G:\2026,G:\2026,7,jxl2jpeg,sRGB,long:2048,screen,ProPhoto-g22,sRGB,16B_JXL,JPG_mobile
```

The whole manifest is checked before anything runs: two rows writing the same file, or an invalid cell, refuse it up front. Every column, what can be left out, and recipes — among them a whole library from masters to phone JPEGs: [docs/README_manifest.md](docs/README_manifest.md#7-a-whole-library-end-to-end-masters-a-16-bit-srgb-copy-phone-jpegs).

---

## Individual Scripts

### Typical workflow (script commands)

```
Capture One
    ↓ Export 16-bit TIFF (sRGB, AdobeRGB, ProPhoto RGB)
jxl_tiff_encoder.py      TIFF → JXL      the master: 16-bit, lossless or near-lossless
    ↓                                    (~31 MB at d=0.1, ~173 MB lossless, 45 MP)
    ├─ jxl_recompressor.py    JXL → JXL       lighter 16-bit copies: sRGB, resized, a higher distance
    ├─ jxl_jpeg_transcoder.py JXL → JPEG/PNG  for sharing or print: colour conversion, resize, sharpening
    └─ jxl_tiff_decoder.py    JXL → TIFF      the TIFF back, in its original profile, to edit again
```

**The encoder only writes masters — by design.** It has no colour conversion, resize or sharpening: a master must be the TIFF, same pixels (up to the distance), same profile, same metadata, so that it can bring the TIFF back and prove it is safe to delete. A setting left on by mistake should never turn an archive into resized, sharpened files that look like masters. Resize, sharpening and colour conversion live where copies are made, from the master: the recompressor (lighter JXLs) and the transcoder (JPEG/PNG). Copies never delete anything and never pass for a master. [Why](docs/README_jxl_tiff_encoder.md#why-the-encoder-never-converts-colour-or-resizes-design-decision).

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
2. Delete the TIFFs, keep only the JXLs — with this toolkit's `--delete-source`: each TIFF is deleted only after its JXL is written and verified (decoded and compared with the TIFF), and a TIFF re-exported during the run is kept. The older [delete-tiff-exports](https://github.com/rsilvabr/delete-tiff-exports) (archived) is a simple script that deletes the `_EXPORT\TIFF16` folders recursively and checks nothing: only for TIFFs whose JXLs you have already verified.

Then back up what you kept. [photo-backup](https://github.com/rsilvabr/photo-backup) copies, for every folder that has an export folder, the JXL master (instead of the TIFF), the RAW or camera JPEG it came from and your Capture One / NX Studio edits — and leaves out the outtakes and the copies this toolkit can make again from the master (sRGB, phone). It was written around one folder layout: the originals beside an export folder (`_EXPORT`, `Lightroom_Export`, … — any name containing "export"), the exports inside it. Run its dry-run first and read what it would copy; a different layout may need its settings, or the script, adapted.

---

## Recommended Settings

### Masters (`jxl_tiff_encoder.py`)

```python
CJXL_DISTANCE = 0.1     # near-lossless, ~31 MB at 45 MP — the default
# CJXL_DISTANCE = 0.05  # finest lossy, ~42 MB — what I use, out of caution
CJXL_EFFORT = 7         # effort 9 gains nothing at these distances (docs/file_sizes.md)
```

Film scans and 8-bit TIFFs get lossless on their own.

### Copies, made from the masters

```powershell
# A light 16-bit sRGB copy (d=0.7 keeps the grain texture)
py jxl_recompressor.py "F:\Photos\2025" --mode 7 --export-subfolder 16B_JXL --export-jxl-folder sRGB --output-icc sRGB --distance 0.7 --sync --rename-from ProPhoto-g22 --rename-to sRGB

# JPEGs for the phone: sRGB, 2048 px, screen sharpening, quality 70
py jxl_jpeg_transcoder.py "F:\Photos\2025" --mode 7 --export-subfolder 16B_JXL --export-jpeg-folder JPG_mobile --force-convert --decode --format jpeg --icc-profile sRGB --resize-long 2048 --sharpen screen --quality 70 --sync --rename-from ProPhoto-g22 --rename-to sRGB
```

The same three steps as manifests and presets: [a whole library, end to end](docs/README_manifest.md#7-a-whole-library-end-to-end-masters-a-16-bit-srgb-copy-phone-jpegs).

### What a re-run redoes

With sync (the wizard's and the presets' default), a run redoes only what changed:

| You change | Master | JXL copy | JPEG / PNG copy |
|---|---|---|---|
| The TIFF (re-exported) | redone | redone (its master is newer) | redone (its master is newer) |
| The distance, up or down | **kept** — run once with `--overwrite` | redone | — |
| The effort | **kept** | redone if higher, kept if lower | — |
| Colour, resize, sharpening (`none`/`screen`/`print`) | — | redone | redone (a resized or sharpened copy) |
| Sharpening sigma / gain / threshold | — | kept | kept |
| JPEG quality | — | — | redone (a resized or sharpened JPEG) |
| PNG bit depth | — | — | **kept** — run once with `--overwrite` |

A master is never redone because a setting changed: it is the archive, and a wrong value in a preset should not re-encode it. [Why each one](docs/behavior_and_limitations.md#what-a-re-run-redoes).

---

## How the ICC profile is preserved

```
Plain cjxl/djxl:   TIFF (ProPhoto, Kodak TRC) → JXL (native primaries) → TIFF with a generic profile
With this toolkit: TIFF (ProPhoto, Kodak TRC) → JXL (+ the original ICC in XMP) → TIFF with the ORIGINAL profile
```

The encoder stores the original ICC profile, base64-encoded, in the JXL's XMP (a `jxlphoto-iccprofile` item in `dc:Relation`; `xmp:CreatorTool` up to v2.18.x), and the decoder's Roundtrip mode puts it back byte for byte. To check a round trip:

```powershell
exiftool -ProfileDescription -ProfileCopyright original.tif roundtrip.tif   # should match
```

XYB, ICC blobs and the table-curve profiles that need special handling: [docs/jxl_color_internals.md](docs/jxl_color_internals.md).

### The encode record

Every JXL the toolkit writes also records how it was encoded, for example `gen=2 | cjxl d=0.05 e=7 | cjxl d=0.7 e=7`. Each `cjxl d= e=` entry is one encode, with its distance and effort, oldest first; `gen=` counts the lossy ones — how many times the photo has lost quality. A file re-encoded from another keeps the old entries and adds its own, so a copy made from a master still shows where it came from. The recompressor reads the record to decide what is worth doing: it asks before an encode that would only lose quality (the same or a finer distance than the file already has) and before a third lossy generation.

The record lives in the file's XMP processing history (`xmpMM:History`), out of the way: your caption stays alone in the photo's Title. To see it in Windows Explorer as well, `--encode-tag comment` copies it into Comments (or `title`, after the caption). Every field the toolkit writes: [docs/metadata_written.md](docs/metadata_written.md#the-encode-record).

---

## Good to know

One line each; the detail is in [docs/behavior_and_limitations.md](docs/behavior_and_limitations.md).

- **Lossy is the default** (`--distance 0.1`, near-lossless, about a tenth of the TIFF); use `--distance 0` for a bit-exact archive. [More](docs/behavior_and_limitations.md#lossy-is-the-default---distance-01)
- **cjxl 0.12 treats every distance up to 0.05 as 0.05**, so smaller values buy nothing there. [More](docs/behavior_and_limitations.md#the-distance-floor-depends-on-your-cjxl-005-on-libjxl-012-001-on-011)
- **Every page of a multi-page TIFF is converted by default**, embedded previews included, except Capture One's. [More](docs/behavior_and_limitations.md#multi-page-tiffs-every-page-is-converted-by-default)
- **The decoder never overwrites an original TIFF**, nor a decode you edited since. [More](docs/behavior_and_limitations.md#the-decoder-never-overwrites-an-original-tiff)
- **Re-runs differ per script:** the TIFF encoder/decoder sync by date, the JPEG transcoder skips existing outputs; a changed distance redoes the copies but not the masters ([table](#what-a-re-run-redoes)). [More](docs/behavior_and_limitations.md#default-re-run-behavior-differs-per-script)
- **Renaming folders:** a re-export after renaming its folder is refused by default; if you rename folders and never delete sources, turn the overwrite guard off. [More](docs/behavior_and_limitations.md#renamed-folders-and-the-overwrite-guard)
- **8-bit TIFFs** are archived lossless when the distance is 0.1 or less, where lossy saves little (`d=0.1`) or comes out larger (`d=0.05`); `--eight-bit-distance same` turns it off. [More](docs/README_jxl_tiff_encoder.md#8-bit-tiffs-are-archived-lossless-at-low-distances-eight_bit_distance)
- **Film scans** are archived lossless by default, and SilverFast recognises the IR channel and dust removal after the round trip; other scanner programs were not tested — test one file first. [More](docs/behavior_and_limitations.md#film-scanners-ir-channel--digital-ice)
- **Viewers:** Windows Explorer thumbnails are slow and not colour-managed ([jxl-thumb](#related-project-fast-jxl-thumbnails-in-explorer) fixes both; the menu reminds you when no JXL thumbnail handler is installed); IrfanView reads the EXIF only from an uncompressed box placed first, which jxl-photo writes by default. [More](docs/behavior_and_limitations.md#viewer-quirks-not-data-loss)
- **New releases:** the menu checks GitHub at most once a day and adds a line when a newer release is out; nothing is downloaded, and `--run-preset` never checks. [More](docs/README_jxl_tools.md#new-release-notice)
- **What goes into your files:** your metadata is copied; the toolkit adds the original ICC, the encode record, a few `jxlphoto-*` markers and one `xmpMM:History` entry per write — no readable path or name. [More](docs/metadata_written.md)

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
| [docs/file_sizes.md](docs/file_sizes.md) | File sizes per camera and for film scans, and what effort 9 buys |
| [docs/metadata_written.md](docs/metadata_written.md) | Every field the toolkit writes into your photos, and what it reveals |
| [docs/jxl_color_internals.md](docs/jxl_color_internals.md) | Deep dive: XYB, ICC blobs vs primaries, troubleshooting |
| [docs/upgrading.md](docs/upgrading.md) | What to check when you upgrade from an older version |
| [docs/version_history.md](docs/version_history.md) | Every release in detail, and the full release table |
| [docs/bug_tracking_since_v1.0.md](docs/bug_tracking_since_v1.0.md) | Every bug fix since v1.0, numbered and dated |
| [docs/new_features_since_v1.0.md](docs/new_features_since_v1.0.md) | Every new feature since v1.0 |

**Testing.** `pytest tests/` runs the test suite (synthetic files, plus real-codec tests that skip when `cjxl`/`djxl`/`exiftool` are missing). [tools/real_photo_battery.py](tools/real_photo_battery.py) runs the four scripts end to end on **copies of your own photos** (`py tools/real_photo_battery.py --fixtures <folder>`; the fixture layout is in its docstring).

---

## What's new in v2.27.0

2026-11-11. Two additions for TIFF archives and one fix; most people have nothing to do.

- **A TIFF's IPTC data reaches the master**: the fields are copied into the XMP where the XMP lacks them, and the decoder writes the IPTC block back byte for byte. A SilverFast scan whose capture date lived only in the IPTC keeps it. Exports from Capture One or Lightroom already carry the same data in their XMP.
- **The encoder's sync compares the content, not only the date**: a TIFF with a newer date but the same bytes (a backup restored, a folder copied) is skipped. `--overwrite` remakes a master on purpose ([upgrading](docs/upgrading.md)).
- **A lossy JPEG → JXL of a JPEG with a broken thumbnail IFD converts** instead of ending in an error.

[Version history](docs/version_history.md#v2270).

### In v2.26.1

2026-11-10. Hardening that closes edge cases; nothing to do when upgrading.

- **A JXL made from a rotated JPEG is shown the right way up even without `jxlinfo`**: the scripts read the orientation from the file's header themselves.
- **PNGs and turned-back JPEGs keep the camera's `InteropIndex`** (the DCF colour-space tag), and a JXL → JPEG run calls exiftool once less per file.

[Version history](docs/version_history.md#v2261).

### In v2.26.0

2026-11-10. One new behaviour and the fixes left open after v2.25.3.

- **A JPEG made from the JXL of a rotated JPEG comes back as the camera wrote it**: the stored pixels and the original EXIF Orientation (it shows the same way up). `--jpeg-orientation upright` or `JPEG_ORIENTATION` keeps v2.25.3's upright JPEG; PNG and TIFF stay upright ([upgrading](docs/upgrading.md)).
- **JXL → PNG of camera JPEGs, and resizing or colour-converting a source with the standard sRGB profile** (camera and phone JPEGs, sRGB TIFF masters), work instead of ending in an error. Nothing to do.
- The decoder's new `--exiftool-ignore-minor`, off by default, for the rare JXL whose maker notes exiftool cannot read; the end of a run lists such files. `jxlinfo` is now part of the dependency check.

[Version history](docs/version_history.md#v2260).

### In v2.25.3

2026-11-10. Fixes from a bug sweep; most close rare cases.

- **A JXL made from a rotated JPEG decodes upright** to JPEG, PNG or TIFF, and its recompressed copies too: the EXIF Orientation is set to 1, since the pixels are already turned. Uses `jxlinfo`, which comes with cjxl/djxl.
- **`--delete-source` keeps what a JXL cannot hold**: a TIFF with Photoshop layers, and a float or animated JXL decoded to TIFF/PNG. A 10/12-bit TIFF is refused instead of archived 16 times darker.
- **A move out of staging that fails is an error** (exit code 1), not a success with the old file in place.
- The wizard follows the recompressor's own `ON_DOWNGRADE`/`ON_REGENERATION`/`ON_SCAN`, and a preset without effort, quality or distance uses the script's setting. What changes on an existing command line: [upgrading](docs/upgrading.md).

[Version history](docs/version_history.md#v2253).

### In v2.25.2

2026-11-10. Hardening that closes edge cases.

- **Two runs started at the same time never write over each other's output**; a lossy JXL → JPEG/PNG `--sync` leaves a same-named camera JPEG/PNG alone ([upgrading](docs/upgrading.md)); every script flushes each output to the disk before deleting the source.

[Version history](docs/version_history.md#v2252).

### In v2.25.1

2026-11-08. Small fixes; nothing to do when upgrading.

- **The new-release notice reaches you even when you close the window after a job**: the check runs in the background and keeps its answer, so the next start shows it (with the day it was checked). `D` in the menu now checks for a new release too.
- **A `TEMP_DIR` that does not exist yet** is created before the JPEG transcoder starts, like the other scripts already did; a dry run says the folder is missing instead of failing its reads.

[Version history](docs/version_history.md#v2251).

### In v2.25.0

- **The TIFF's byte order is kept**: a big-endian TIFF (SilverFast scans,
  some Mac programs) gives a big-endian master and decodes back to a
  big-endian TIFF, and every copy keeps its master's order. Only how the EXIF
  numbers are stored changes, not their values; little-endian TIFFs (Capture
  One, Lightroom) behave as before.
- **A new JPEG quality redoes the JPEG copies** on the next sync, like a new
  size or sharpening. JPEGs made before are kept; run once with
  `--overwrite` to redo them ([Upgrading](docs/upgrading.md#v2250-the-tiffs-byte-order-is-kept-a-new-jpeg-quality-redoes-the-jpeg-copies)).

[Version history](docs/version_history.md#v2250).

### In v2.24.0

- **The cjxl record no longer shows as the photo's Title** in Windows
  Explorer: it is kept in the file's XMP History, and your caption stays your
  own. `--encode-tag comment` shows it in Explorer's Comments,
  `--encode-tag title` keeps the old behaviour. Older files are read as they
  are ([Upgrading](docs/upgrading.md#v2240-8-bit-tiffs-are-archived-lossless-at-d01-and-below-the-encode-record-leaves-the-title)).
- **8-bit TIFFs are archived lossless at `d=0.1` and below**: lossless is
  small for them (43 MB for 45 MP), and `d=0.05` would come out larger.
  `--eight-bit-distance same` keeps the old behaviour
  ([Upgrading](docs/upgrading.md#v2240-8-bit-tiffs-are-archived-lossless-at-d01-and-below-the-encode-record-leaves-the-title)).
- **The wizard shows the typical file size next to every distance** (45, 24
  and 12 MP), and "your distance" shows them all, from 0 to 25.
- `d=0.7` (keeps the grain texture) in both distance lists, `d=5` for small
  copies; shorter names and lighter notes. The menu also says when
  [jxl-thumb](https://github.com/rsilvabr/jxl-thumb) has a newer release.

[Version history](docs/version_history.md#v2240).

### In v2.23.0

- **Derivatives (sRGB, resized) and JXL -> TIFF are about 35-40% faster**: a
  temp file in the pipeline is no longer compressed. Same pixels.
- **`--export-jpeg-folder`** names the folder a JXL -> JPEG/PNG run of modes
  6/7 writes (e.g. `JPG_mobile`); the wizard and the manifests use it too.
- **The wizard reads better**: choices, lists and the review in tables, one
  name per mode everywhere, actions grouped by what they do, a blank line
  before every question.
- The update check never makes the menu wait; jxl-thumb is recommended when
  Microsoft's extension makes the thumbnails; both reminders can be turned
  off in Settings.

[Version history](docs/version_history.md#v2230).

### In v2.22.1–v2.22.5

2026-11-04. Every wizard question goes through one question layer. Distances up to 25 (cjxl's own limit) are accepted, not only up to 15. The wizard names each distance it offers: for masters (lossless,
finest lossy, near-lossless, high-quality lossy, visually lossless for
viewing) and for smaller copies (cjxl's recommended 0.5 to 3). The review
screen shows the distance film scans get, right below the run's. Enter still
takes your default distance. [Version history](docs/version_history.md#v2225).

### In v2.22.0

The wizard is organised by task, and every option is within reach.

- **A New job asks what to do, where and how**, then shows one review screen with every option and where its value came from: `[r]` runs it, `[d]` runs it dry first, `[a]` shows all the options — including the ones the wizard never asked about (`--buffering`, `--restamp-then-sync`, …). The menu numbers changed ([Upgrading](docs/upgrading.md#v2220-a-jxl--jpeg-never-deletes-the-jxl-modes-67-anchor-below-the-input-and-leave-derivatives-out)); presets and the last job carry over by themselves.
- **New menus** for renamed or moved folders, the ICC test cache and the scripts' own settings (changed without editing the scripts).
- **A JXL → JPEG (or 8-bit PNG) never deletes the JXL**; modes 6/7 anchor on the `_EXPORT` below the input and leave derivatives out.
- The menu says when a newer release is out (checked at most once a day).

Full notes: [version history](docs/version_history.md#v2220).

### Recent releases

| Version | Date | Highlights |
|---------|------|------------|
| **v2.27.0** | 2026-11-11 | A TIFF's IPTC data reaches the master and comes back in the decoded TIFF; the encoder's sync skips a TIFF with a newer date and the same content; a lossy JPEG → JXL of a JPEG with a broken thumbnail IFD converts |
| [v2.26.1](docs/version_history.md#v2261) | 2026-11-10 | A rotated JPEG's archive is shown the right way up even without `jxlinfo`; PNGs and turned-back JPEGs keep the camera's `InteropIndex` |
| [v2.26.0](docs/version_history.md#v2260) | 2026-11-10 | The JPEG of a rotated JPEG's archive comes back as the camera wrote it; JXL → PNG of camera JPEGs and resizing sources with the standard sRGB profile work; `--exiftool-ignore-minor` |
| [v2.25.3](docs/version_history.md#v2253) | 2026-11-10 | A rotated JPEG's archive decodes upright; `--delete-source` keeps layered TIFFs and float/animated JXLs; 10/12-bit TIFFs refused; a failed move out of staging is an error |
| [v2.25.2](docs/version_history.md#v2252) | 2026-11-10 | Two runs at once never write over each other's output; a lossy decode with `--sync` leaves the camera's JPEG alone; flush to disk before every delete |
| [v2.25.1](docs/version_history.md#v2251) | 2026-11-08 | The new-release notice shown on the next start; `D` checks for releases; a missing `TEMP_DIR` created by the JPEG transcoder too |
| [v2.25.0](docs/version_history.md#v2250) | 2026-11-06 | The TIFF's byte order kept in masters, copies and decoded TIFFs; a new JPEG quality redoes the JPEG copies |
| [v2.24.0](docs/version_history.md#v2240) | 2026-11-06 | The encode record leaves the Title for the XMP History; 8-bit TIFFs lossless at `d=0.1` and below; typical file sizes next to every distance in the wizard |
| [v2.22.5](docs/version_history.md#v2225) | 2026-11-04 | Each distance named in the wizard, masters and smaller copies; the film-scan distance on the review screen; distances up to 25 |
| [v2.22.1](docs/version_history.md#v2221) | 2026-11-04 | Every wizard question on one question layer; same questions and command lines |
| [v2.22.0](docs/version_history.md#v2220) | 2026-11-04 | The wizard by task: a New job and one review screen with every option; new menus; a JXL → JPEG never deletes the JXL; modes 6/7 anchor below the input |
| [v2.21.0](docs/version_history.md#v2210) | 2026-11-04 | JPEG/PNG of cautious-strategy masters carry their real profile; less memory per worker; a failed metadata copy keeps the source |
| [v2.20.0](docs/version_history.md#v2200) | 2026-11-03 | Hardening: an unreadable provenance record is refused, not overwritten; modes 6/7 skip the other scripts' folders; planning reports progress |
| [v2.19.2](docs/version_history.md#v2192) | 2026-11-03 | IrfanView shows the EXIF of JPEG → JXL files; menu option 8 repairs older ones in place, proving the same JPEG comes back |
| [v2.19.1](docs/version_history.md#v2191) | 2026-11-02 | IrfanView shows the EXIF of new JXLs again; menu option 9 repairs files written by v2.17.0–v2.19.0 in place |
| [v2.18.1](docs/version_history.md#v2181) | 2026-11-01 | A recompressor sync plans only what it will process: up-to-date files are skipped without being read |
| [v2.17.0](docs/version_history.md#v2170) | 2026-10-24 | Film scans archived lossless and never recompressed lossy unless asked; they keep what their scanner software needs (whole XMP, private tags); thumbnail pages kept by default, except Capture One's |

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

Getting there required finding and fixing several bugs that appear because of the specific combination of programs I use (Capture One, cjxl, exiftool, IrfanView). Those bugs and their fixes are documented in [`docs/bugs_fixes_explained.md`](docs/bugs_fixes_explained.md).

---

## Related project: jxl-photo as a Windows program

[jxl-photo-exe](https://github.com/rsilvabr/jxl-photo-exe) packages this toolkit as `jxl-photo.exe`: no Python to install, and cjxl/djxl, ExifTool, ImageMagick and jxl-thumb come in the same folder. Unzip and double-click for the wizard, or use the same command lines as here (`jxl-photo.exe encode ...`). The five scripts inside are byte-for-byte those of a jxl-photo release, and it passes the same real-photo battery.

This Python version, the one I run on my own archive every day, remains by far the more tested. Use the program if you would rather not install Python; otherwise use the scripts here.

---

## Related project: fast JXL thumbnails in Explorer

[jxl-thumb](https://github.com/rsilvabr/jxl-thumb) is a Windows thumbnail provider for the archives this toolkit writes (and any other JPEG XL). It reads only the small 1:8 image a lossy JXL stores at the start of the file and converts it to sRGB, so Explorer thumbnails come out about 40× faster than with the Microsoft extension on 25–45 MP masters (the preview pane about 14×), with the right colours for ProPhoto and Adobe RGB. Existing files work as they are; install is per user, no admin rights.

---

## Related project: a simpler, TIFF-only alternative

If this toolkit's setup is more than you want to deal with, [tiff-workflow](https://github.com/rsilvabr/tiff-workflow) is a PowerShell toolkit (with an optional Python wizard UI) that losslessly re-compresses TIFFs with ZIP/Deflate compression — pixel data stays identical, only the compression is re-optimized. It also covers related TIFF chores: copying EXIF from JPEG to TIFF (Fuji S3/S5 Pro workflow), diagnosing padded 16-bit files, and generating colour-managed sRGB thumbnails.

- **What you need:** PowerShell 7 (or Windows PowerShell 5.1), ImageMagick, ExifTool — Python 3.9+ with `rich` only if you want the wizard UI
- **What's NOT needed:** Python or Python packages for the direct PowerShell usage, libjxl (cjxl/djxl)

A 45 MP photo (medians, MB):

| Format | 16-bit | 8-bit |
|--------|:------:|:-----:|
| Uncompressed TIFF | 273 | 136 |
| **ZIP/Deflate (tiff-workflow)** | 227 (~17% smaller) | 61 (~55% smaller) |
| JXL lossless (this toolkit) | 173 (~37% smaller) | 43 (~68% smaller) |
| **JXL `d=0.1` (this toolkit)** | 31 (~89% smaller) | 43 — kept lossless: lossy would save only ~10 MB |

**Trade-off:** easier to install, but much less compression, and the output stays a TIFF. Still better than nothing. Once you are comfortable with ImageMagick and ExifTool, the setup here is the same two tools plus Python and libjxl.

---

## Related project: selective backup (photo-backup)

[photo-backup](https://github.com/rsilvabr/photo-backup) backs up what this toolkit leaves you with. For every folder that has an export folder it copies the exports (the JXL master instead of the TIFF), the originals of the exported photos (RAW, camera JPEG, sidecars) and your Capture One / NX Studio edits, mirroring the folder tree on the destination. It leaves out the outtakes and the copies jxl-photo can make again from the master (sRGB, phone). The source is only read, nothing on the destination is ever deleted, and a later run can verify that the backup still matches.

It was written around one folder layout: the originals beside an export folder (`_EXPORT`, `Lightroom_Export`, … — any name containing "export"), the exports inside it. Run its dry-run first and read what it would copy; a different layout may need its settings, or the script, adapted.

---

## Disclaimer

These tools were made for my personal workflow.
Use at your own risk — I am not responsible for any issues you may encounter.

However, if you find any bugs, feel free to report them to me — I will gladly try my best to improve this project.

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
