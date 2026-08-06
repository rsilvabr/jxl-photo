# jxl_tiff_encoder.py

Batch TIFF 16-bit → JPEG XL converter. Encodes TIFF files to JXL format with 
configurable quality (lossless or lossy), preserves full EXIF/XMP metadata, 
and **embeds the original ICC color profile as XMP metadata** for perfect 
round-trip preservation.

Works with any 16-bit TIFF — Capture One exports, NX Studio, Photoshop, or 
standard uncompressed TIFFs from various sources.

**Key feature:** ICC profile embedding. 
```
When paired with `jxl_tiff_decoder.py`, the exact original ICC profile is 
preserved even for **lossy JXL files**, which would otherwise lose 
the detailed ICC information (gamma curves, copyright, etc.).
```

---

## Requirements

```
Python 3.9+
pip install tifffile numpy imagecodecs
cjxl  →  https://github.com/libjxl/libjxl/releases
exiftool  →  https://exiftool.org
```

Both `cjxl.exe` and `exiftool.exe` must be on your PATH.

### Download the Correct Files

| Tool | Download | What to Get |
|------|----------|-------------|
| **cjxl** | https://github.com/libjxl/libjxl/releases | `jxl-x64-windows-static.zip`  **(NOT `jxl-x64-windows.zip` which has only DLLs)** |
| **exiftool** | https://exiftool.org | `exiftool-XX.XX_64.zip`  **(Windows .zip, NOT .tar.gz source)** |

### exiftool Setup

> **Note:** The scripts automatically detect both `exiftool.exe` and `exiftool(-k).exe`. **Renaming is no longer required**, but still works if you prefer.

The Windows download comes as `exiftool(-k).exe`. If you want to rename it anyway:

```powershell
# Option A: Rename
Rename-Item "C:\tools\exiftool\exiftool(-k).exe" "exiftool.exe"

# Option B: Duplicate and rename (keeps original)
Copy-Item "C:\tools\exiftool\exiftool(-k).exe" "C:\tools\exiftool\exiftool.exe"
```

### Add to PATH

**Replace with YOUR actual paths:**

```powershell
$myPaths = @(
    "C:\tools\libjxl\bin",    # where cjxl.exe is
    "C:\tools\exiftool"        # where exiftool.exe / exiftool(-k).exe is
)
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", ($myPaths -join ";") + ";$p", "User")
# Restart PowerShell after this!
```

### Verify
```powershell
cjxl --version      # JPEG XL encoder v0.11.x
exiftool -ver       # 13.xx
```

---

## Quick start

```powershell
# ── The easy way — mode 0, no flags needed ──────────────────────
# Single file, in-place
py jxl_tiff_encoder.py "F:\Photos\photo.tif"

# Single file → specific output folder
py jxl_tiff_encoder.py "F:\Photos\photo.tif" "F:\output"

# Whole folder, in-place (flat — subfolders not touched)
py jxl_tiff_encoder.py "F:\Photos"

# Whole folder → specific output folder (flat)
py jxl_tiff_encoder.py "F:\Photos" "F:\output"

# ── Other modes ──────────────────────────────────────────────────
# Capture One _EXPORT workflow (mode 7) — most common for C1 users
py jxl_tiff_encoder.py "F:\2024" --mode 7

# Sync — only reconvert TIFFs newer than existing JXL
py jxl_tiff_encoder.py "F:\2024" --mode 7 --sync

# 16 parallel workers
py jxl_tiff_encoder.py "F:\2024" --mode 7 --workers 16

# Mode 8 — in-place recursive: JXL next to each TIFF, all subfolders
py jxl_tiff_encoder.py "F:\2024" --mode 8
```

---

## Key settings

Edit at the top of the script:

```python
CJXL_DISTANCE = 0.1
# 0   = mathematically lossless (pixel-perfect, ~173MB for 45MP)
# 0.05 = near-lossless (~47MB, imperceptible difference) ⭐ RECOMMENDED for archive
# 0.1 = near-lossless (~34MB, imperceptible difference)  ⭐ ALSO RECOMMENDED for archive
# 0.5 = high quality lossy (~13MB) — recommended starting point (libjxl authors)
# 1.0 = "visually lossless" per libjxl documentation (~8MB)

CJXL_EFFORT = 7
# Compression effort (1-10). Controls file size, NOT quality.
# 7 is the sweet spot for camera photos.
# Effort 8-10 is much slower and can produce larger files for high-ISO images.

CJXL_BUFFERING = None
# [libjxl >= 0.12 only] Encoder buffering level passed to cjxl (also --buffering CLI).
# None = use cjxl's own default (2) — the fast path (default).
# 0    = best compression, but ~6x slower on large lossless TIFFs for only
#        ~1.2% smaller files (see the v1.8.0 release notes benchmark on GitHub).
# Ignored automatically when cjxl is < 0.12 (flag doesn't exist there).

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
# Can also be set via --encode-tag CLI argument (xmp/software/off)
# NOTE: When EMBED_ICC_IN_JXL is True, the ICC goes to XMP:CreatorTool
# and encoding params are concatenated to dc:Description.

EMBED_JPEG_THUMBNAIL = False
# Embed a JPEG thumbnail (256px) in the JXL file EXIF metadata.
# True  → creates a 256px JPEG preview and embeds it as EXIF thumbnail
#          Increases file size by ~10-30KB per image
#          Useful for fast preview in IrfanView, XnView, digiKam
# False → no embedded thumbnail (default, smaller files)
# Can also be set via --embed-thumbnail CLI argument.

CJXL_MODULAR = False
# False (default) — lossy uses VarDCT encoder + XYB colorspace.
# True  — forces Modular encoder for lossy (--modular=1).
#   Less efficient for photos, but good for screenshots/UI art.
#   Use only if you need non-XYB encoding for compatibility reasons.

D50_PATCH_MODE = "auto"
# D50 illuminant patch for Capture One ICC compatibility.
# Capture One has a bug where the D50 illuminant values are slightly off
# (rounding error). This patch fixes them for cjxl compatibility.
# "on"   → Always apply the patch
# "off"  → Never apply the patch (use original ICC values)
# "auto" → Only apply if source software matches D50_PATCH_SOFTWARE_LIST
# Can also be set via --d50-patch CLI argument (on/off/auto)

D50_PATCH_SOFTWARE_LIST = ["capture one", "captureone"]
# Software names that trigger D50 patch when D50_PATCH_MODE="auto".
# Case-insensitive matching. Add your own software here if it has the same ICC bug.
# The list is checked against EXIF Software field.

CLEANUP_XMP_ICC_MARKER = False
# Remove legacy ICC markers from XMP if present.
# True  → clears xmp-icc:all and xmp-photoshop:ICCProfile tags that might conflict
# False → keeps existing ICC markers (default)

USE_RAM_FOR_PNG = True
# True  → PNG intermediate stays entirely in RAM (faster; RAM scales with
#         megapixels, not with a fixed per-worker figure — see Performance)
# False → PNG is written to disk in TEMP_DIR (useful if RAM is limited)

PIL_MAX_IMAGE_PIXELS = None
# PIL's decompression bomb protection limit (prevents DOS attacks with malicious images).
# None  → Disable the limit completely (recommended for trusted local files/panoramas)
# N     → Maximum number of pixels (e.g., 500_000_000 for ~500MP limit)
# The default PIL limit (~89MP) is too low for large panoramas. Set to None for photography workflows.

TEMP2_DIR = None
# Staging SSD for output JXLs. Separates read I/O (HDD with TIFFs) from write I/O.
# None (default) → write directly to the final destination.
# Example: r"E:\staging"
# Files are moved to their final destination after each folder group completes.
# Can also be set via --staging CLI argument (overrides this variable).

OVERWRITE = "smart"
# False   → skip if JXL already exists (safe for resuming)
# True    → always overwrite
# "smart" → same as --sync: reconvert only if TIFF is newer than JXL

DELETE_SOURCE = False
# [Mode 8 only] Whether to delete the source TIFF after successful encode.
# WARNING: irreversible. Only enable after testing on a small batch first.

# — Safety (mode 8 + DELETE_SOURCE only) —
DELETE_CONFIRM = True
# True  → require interactive confirmation before deleting source files
# False → skip confirmation (for automation only)
```

#### Safety confirmation (mode 8 + DELETE_SOURCE)
```
When `DELETE_SOURCE = True` and `DELETE_CONFIRM = True`:
- **Lossless:** type `yes` to confirm
- **Lossy:** type the current time in `HHMM` format (forces conscious decision)
```



---

## Modes 6 and 7 — ONLY files inside `_EXPORT`

**Modes 6 and 7 ONLY process files inside folders whose names start with or end with `_EXPORT` (case-insensitive). Everything outside is IGNORED.**

```
E:\sessao\
├── foto1.tif          ← NOT processed (outside _EXPORT)
├── foto2.tif          ← NOT processed (outside _EXPORT)
└── _EXPORT\
    ├── folder1\
    │   └── img.tif    ← PROCESSED ✓
    ├── folder2\
    │   └── img.tif    ← PROCESSED ✓
    └── folder3\sub\
        └── img.tif    ← PROCESSED ✓
```

**Mode 6** — processes ALL TIFFs under ALL `_EXPORT` folders.

**Mode 7** — only TIFFs inside a specific subfolder of `_EXPORT` (configurable via `EXPORT_TIFF_SUBFOLDER`; default is `""`, which processes all subfolders inside `_EXPORT`).

```
Mode 7 example with EXPORT_TIFF_SUBFOLDER = "16B_TIFF":
session/_EXPORT/16B_TIFF/photo.tif → session/_EXPORT/16B_JXL/photo.jxl  ✓
session/_EXPORT/AdobeRGB/photo.tif → ignored
```

---

## Output modes

| Mode | Input | How it finds files | Output location | Example |
|------|-------|-------------------|----------------|---------|
| `0` | File or directory | Flat (non-recursive) — only files in the given folder | In-place (flat, non-recursive) | `photo.jxl` |
| `1` | File or directory | Flat (non-recursive) — only files in the given folder | `converted_jxl/` subfolder next to source | `.../converted_jxl/photo.jxl` |
| `2` | Directory | Recursive — all subfolders | Flat → output_dir (recursive) | `output_dir/photo.jxl` |
| `3` | Directory | Recursive — all subfolders | `JXL_16bits/` inside each TIFF folder | `.../TIFF/JXL_16bits/photo.jxl` |
| `4` | Directory | Recursive — all subfolders | Rename folder `TIFF` → `JXL` | `.../Export_JXL/photo.jxl` |
| `5` | Directory | Recursive — all subfolders | Sibling folder `JXL_16bits/` | `.../JXL_16bits/photo.jxl` |
| `6` | Directory | Recursive, **only inside `EXPORT_MARKER`** (default: `_EXPORT`) | ONLY TIFFs INSIDE `EXPORT_MARKER` — ignores everything outside. Marker name configurable. | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `7` | Directory | Recursive, **only inside specific `EXPORT_MARKER` subfolder** (configurable) | Like mode 6 but only specific `EXPORT_MARKER` subfolder. Both marker and subfolder are configurable. | `.../session/_EXPORT/16B_JXL/photo.jxl` |
| `8` | File or directory | Recursive — walks all subfolders | In-place **recursive** — JXL next to each TIFF | `.../session/photo.jxl` |

> **Mode 2 note:** with a directory input, output goes flat to `output_dir` (or the input folder itself if no output is given). With a **single file** input and no output argument, mode 2 writes to `converted_jxl/` next to the file (same as mode 1).

---

## CLI reference

```
py jxl_tiff_encoder.py <input> [output] [options]

Arguments:
  input           Input root folder or file
  output          Output folder (mode 0 only)

Options:
  --mode 0-8      Output folder mode (default: 0)
  --workers N     Parallel threads (tested up to 32 on a Ryzen 9 5950X).
                  RAM is the real ceiling — see "RAM per worker" in Performance
  --overwrite     Always overwrite existing JXLs
  --sync          Reconvert only TIFFs newer than their JXL
                  (wins over --overwrite if both are given)
  --distance N    JXL distance (0=lossless, 0.1=near-lossless, default: from script)
  --effort 1-10  Compression effort (default: from script setting)
  --buffering 0-3 [libjxl >= 0.12] cjxl buffering level (default: off = use cjxl default;
                  0 = best compression, much slower on large lossless images,
                  and ~2.4x the RAM per worker)
  --ram           Keep PNG intermediate in RAM (faster, more memory)
  --no-ram        Write PNG intermediate to disk (slower, less memory)
  --delete-source Delete source TIFFs after successful encode (mode 8 only)
  --delete-confirm-off  Skip the interactive delete confirmation (for wrappers/
                   automation that already asked the user)
  --export-subfolder NAME
                   [Mode 7] Only process TIFFs inside this subfolder of the
                   export marker (default: script setting, empty = all)
  --staging DIR   Staging directory for output JXLs (reduces HDD seek contention)
  --encode-tag      Where to record encoding params: xmp (default), software, off
  --d50-patch       D50 illuminant patch: on (always), off (never), auto (detect)
  --icc-png-strategy cautious|heuristic|always|skip
                    How to embed ICC in the PNG intermediate for lossy encoding (default: cautious)
  --strip           Strip all metadata from output (no EXIF/XMP preservation;
                    also disables the multi-page reconstruction and grayscale
                    markers, so split pages decode as standalone files and
                    faithful multipage/grayscale reconstruction is not possible)
  --embed-thumbnail Embed a 256px JPEG thumbnail in EXIF for fast preview (~20KB)
  --dry-run         Preview operations without converting
  --no-preflight  Skip the pre-run space estimate. The estimate encodes three
                  small crops of your own files to measure this batch's real
                  compression, then projects it against the free space on the
                  staging and destination drives. It only warns, never blocks,
                  and is skipped automatically below 5 GB of input
  --clean-staging Before converting, delete staging leftovers from EARLIER runs
                  (failed outputs kept for inspection). Only files this tool
                  wrote, never subfolders, and nothing touched in the last hour
                  (a concurrent run may still own it). Sweeps the EFFECTIVE
                  staging dir, so it works with TEMP2_DIR set in the script and
                  no --staging flag. Never runs under --dry-run
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success (all files converted or legitimately skipped) |
| `1` | One or more files failed |
| `2` | Aborted (e.g. duplicate output destinations, invalid arguments) |
| `3` | User declined the delete-source confirmation |

**D50 Patch option:**
```powershell
# Force D50 patch on all files
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --d50-patch on

# Disable D50 patch entirely
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --d50-patch off

# Auto-detect based on EXIF Software (default behavior)
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --d50-patch auto
```

---

## ICC Profile Preservation

### Why This Matters

When converting TIFF → **lossy JXL** (`d>0`), the `cjxl` encoder by default:
1. Converts the ICC color profile to **native primaries** (for efficient encoding)
2. The original ICC detail (TRC curves, copyright, etc.) is **discarded** in favor of compact primaries

The JXL format itself supports full ICC in lossy mode, but the reference encoder 
optimizes for size unless explicitly configured otherwise.

#### Without ICC embedding:
```
TIFF (ProPhoto ICC) → JXL (lossy, primaries only) → TIFF (generic ICC from primaries)
```

#### With ICC embedding (`EMBED_ICC_IN_JXL = True`, default):
```
TIFF (ProPhoto ICC) → JXL (lossy + XMP with base64 ICC) → TIFF (original ProPhoto ICC restored)
```
**OBS: If all you need is small file sizes, disable this funcition: it leads to bigger files.**

---

### How It Works

1. **Extract ICC** from source TIFF using exiftool (original ICC for XMP, patched for PNG)
2. **Base64-encode** the ICC profile
3. **Embed in XMP** metadata (`xmp:CreatorTool` field with `ICC:` prefix)
4. **Encoding params** (cjxl d=0.1 e=7) go to `dc:description` (visible in Windows Properties)

When converting back with `jxl_tiff_decoder.py`:
1. Extract ICC from XMP metadata
2. Apply to output TIFF
3. Clean up XMP (remove base64 data, keep encoding params)

---

### Technical Details

The embedded ICC is stored as:
```xml
<xmp:CreatorTool>ICC:AAADrEtDTVMCEAAAbW50clJHQiBYWVogB84A...</xmp:CreatorTool>
<dc:description>
  <rdf:Alt>
    <rdf:li xml:lang="x-default">cjxl d=0.1 e=7</rdf:li>
  </rdf:Alt>
</dc:description>
```

The base64 string is not human-readable but preserves the **exact binary ICC data**.

---

### Important: ICC Extraction Strategy

The script uses **two separate ICC extractions**:

1. **ICC for PNG (cjxl encoding)**: Patched D50 illuminant for Capture One compatibility
2. **ICC for XMP (preservation)**: Original unmodified ICC for perfect round-trip

This ensures:
- cjxl receives a compatible ICC for encoding
- The exact original ICC is preserved for restoration

→ See [JXL Color Internals](jxl_color_internals.md) for more technical details.

---

## D50 Illuminant Patch

### What is it?

Capture One (and some other software) **may create** ICC profiles with a **slightly incorrect D50 illuminant value** due to a rounding error. The ICC specification defines D50 as:
- X = 0.9642, Y = 1.0000, Z = 0.8249

But Capture One writes:
- X = 0.964202, Y = 1.000000, Z = 0.824905

This tiny difference causes `cjxl` to fail or produce warnings when encoding with the ICC profile.

### How the patch works

The script automatically **corrects the D50 illuminant bytes** in the ICC profile before passing it to cjxl:

```
Original ICC → Patch D50 bytes → cjxl encoding
      ↓                              ↓
  XMP storage                    JXL file
```

### Configuration

**Three modes available:**

| Mode | Behavior |
|------|----------|
| `on` | Always apply D50 patch (forces correction on all files) |
| `off` | Never apply D50 patch (use original ICC values) |
| `auto` | Only apply if EXIF Software matches known buggy software (default) |

**Default detection list:**
```python
D50_PATCH_SOFTWARE_LIST = [
    "capture one",
    "captureone",
    # "my software",  # <-- add more software names here (uncomment to enable)
]
```

You can customize this list in the script settings to add other software with the same bug.

### Summary output

After conversion completes, the script shows D50 patch statistics:

```
Done: 42 OK | 0 overwrites | 0 skipped | 0 errors
D50 patch: 15 applied | 27 skipped (mode: auto)
```

This helps you verify that the auto-detection is working correctly for your files.

#### `skipped` vs `corrupt`

`skipped` counts **only** files you asked to skip — `--multipage-mode skip` on a
multi-page TIFF, a thumbnail-only file under `--thumbnail-mode exclude`, or a file
outside the export marker in modes 6/7.

A file that simply cannot be read is reported apart, because no setting asked for
it to be dropped:

```
Done: 41 OK | 0 overwrites | 0 skipped | 0 errors
WARNING | Corrupt: 1 file(s) had no readable pages and were NOT converted:
WARNING |   -> G:\2026\260512_Recife\_EXPORT\scan_099.tif
```

The test is that the page analyzer found neither a real page nor a thumbnail — a
healthy TIFF always has at least one page, so there is no guessing involved.

**Corrupt files do not change the exit code.** A damaged input is not a failed
run: exit `1` still means the conversion failed on a file, so automation reading
exit codes does not start firing on broken photos. Run a manifest and the same
files appear under `CORRUPT / UNREADABLE` in the end-of-run block (see
[README_jxl_tools.md](README_jxl_tools.md)).

---

## Scanner ICC / Lossy Encoding Workaround

Some scanner ICC profiles (e.g. SilverFast `SFprofT`, VueScan) are very large (`> 50 KB`) and contain huge LUTs. When `cjxl` encodes a lossy JXL (`d > 0`) it must convert the pixels to XYB through the ICC profile. With these scanner profiles the conversion can produce extremely dark or almost-black images, even though the same profile works fine in Photoshop / Lightroom / IrfanView.

The workaround is to **not embed the ICC profile in the intermediate PNG's `iCCP` chunk** during lossy encoding. The pixel data is then treated as plain RGB, the JXL encodes correctly, and the original ICC is still preserved as base64 metadata in the JXL (`XMP-xmp:CreatorTool`). The decoder re-attaches the original ICC when it restores the TIFF, so the round-trip file is visually identical to the original.

| Strategy | Behavior | When to use |
|---|---|---|
| `cautious` (default) | Test each unseen ICC with a small 8/16-bit round-trip and cache the result. Avoids false positives from the size heuristic. | Mixed camera + scanner workflows; recommended default. |
| `heuristic` | Skip `iCCP` for profiles ≥ 50 KB **or** with ICC class `scnr` (scanner). Otherwise embed normally. | Mixed camera + scanner workflows when you need a faster rule-based fallback. |
| `always` | Always embed the ICC in the PNG. | Normal camera images where you want the JXL to display correctly in viewers. |
| `skip` | Never embed the ICC in the PNG. | Maximum safety for any source; JXL colors may look wrong until decoded. |

> **Note:** the `skip` strategy here means "do not embed the ICC in the PNG intermediate". It does **not** mean "skip the JXL conversion". A future release may add a separate option to copy the original TIFF instead of converting when the ICC is problematic.

> **Note:** the JXL file itself may display with shifted colors in some viewers when the ICC is skipped, because the JXL falls back to native/sRGB primaries. For scanner workflows the JXL is treated as a long-term backup container; the reconstructed TIFF is the final image.

### Example CLI usage

```powershell
# Heuristic: skip iCCP for large/scanner profiles (default)
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --distance 0.1 --icc-png-strategy heuristic

# Always embed the ICC (best for normal camera images; JXL displays correctly in viewers)
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --distance 0.1 --icc-png-strategy always

# Never embed the ICC (safest for any source, but JXL colors may look wrong until decoded)
py jxl_tiff_encoder.py "F:\Scanner_Archive" --mode 7 --distance 0.1 --icc-png-strategy skip

# Cautious: round-trip test each unseen ICC and cache the result (recommended for mixed libraries)
py jxl_tiff_encoder.py "F:\Photos" --mode 7 --distance 0.1 --icc-png-strategy cautious
```

For more details, see the v1.7.0 release notes and `docs/bugs_fixes_explained.md`.

---

## Relationship with jxl_tiff_decoder.py

These scripts are designed to work as a pair:

```powershell
# Encode: TIFF → JXL (with ICC embedding)
py jxl_tiff_encoder.py "photo.tif" --mode 0

# Decode: JXL → TIFF (ICC restored)
py jxl_tiff_decoder.py "photo.jxl" --mode 0
```

**For best results:**
- Use `EMBED_ICC_IN_JXL = True` (default) in `jxl_tiff_encoder.py`
- Both scripts detect and handle the embedded ICC automatically

---

## XMP Preservation (Fixed in this version)

### The XMP Overwrite Bug (Fixed)

Previous versions had a bug where XMP metadata was overwritten:
1. First, EXIF/XMP was copied from TIFF
2. Then, a second pass overwrote ALL XMP with just the ICC data

**Result**: Original ratings, keywords, and descriptions were lost!

### The Fix

This version uses **targeted XMP updates**:
- `-xmp-dc:Description=` for encoding params (concatenated with existing dc:description)
- `-xmp-xmp:CreatorTool=` for ICC data (base64-encoded ICC profile)
- All other XMP tags preserved via `-tagsfromfile`

**Result**: Original metadata + encoding info + ICC all coexist!

---

## Performance

With `USE_RAM_FOR_PNG = True` (default), the PNG intermediate lives entirely
in RAM. Disk I/O per file = read TIFF + write JXL.

With `--staging` (or `TEMP2_DIR`) set to a separate SSD, JXLs are written to fast storage during conversion
and moved in bulk at the end — eliminates random write contention on HDD collections.

### RAM per worker

Measured with libjxl **v0.12.0**: peak RSS of the whole process tree (Python +
`cjxl`) with `--workers 1`, lossless (`--distance 0`), `USE_RAM_FOR_PNG = True`,
on real 16-bit ProPhoto exports. Divide your RAM budget by these to pick
`--workers`.

| Source | Effort | Buffering | RAM per worker | Time | JXL size |
|---|---|---|---|---|---|
| 24 MP (6048×4032) | 7 | default | 0.87 GB | 19.7 s | 68.8 % |
| 24 MP | 9 | default | 0.99 GB | 79.9 s | 68.6 % |
| 24 MP | 7 | `0` | 2.31 GB | 141.5 s | 67.8 % |
| 24 MP | 9 | `0` | 3.14 GB | 351.0 s | 67.0 % |
| 45 MP (8256×5504) | 7 | default | 1.73 GB | 36.8 s | 66.3 % |
| 45 MP | 9 | default | 1.55 GB | 168.7 s | 66.8 % |
| 45 MP | 7 | `0` | 4.24 GB | 278.6 s | 65.4 % |
| 45 MP | 9 | `0` | 5.05 GB | 632.9 s | 65.4 % |
| 93 MP scan (11170×8355) | 7 | default | 3.19 GB | 75.4 s | — |
| 93 MP scan | 9 | default | 3.32 GB | 415.8 s | — |

JXL size is a percentage of the source TIFF. Peak-RSS sampling has a few
percent of noise: the 45 MP effort-9 figure landing just under effort 7 is
noise, not a real inversion.

**Megapixels drive RAM, not effort.** Effort 7 → 9 barely moves memory but
costs 2–5× the time. Budget roughly **35–40 MB of RAM per megapixel, per
worker**.

**The safe worker count depends on image size, not just on the machine.** On
64 GB, at effort 9 with default buffering:

| Source | 30 workers | Comfortable ceiling |
|---|---|---|
| 24 MP | ≈ 30 GB | 30+ |
| 45 MP | ≈ 47 GB — tight | ~25 |
| 93 MP scan | ≈ 100 GB — pages badly | ~15 |

**`--buffering 0` does not pay for itself.** It costs 2.5–3.3× the RAM and
3.8–7.6× the time to return **1–1.6 percentage points** of file size. At 45 MP
it gives the same 65.4 % at effort 7 and effort 9, so pairing it with effort 9
buys nothing at all. This is why it is off by default.

### Lossy (`--distance 0.05`): same RAM, a fraction of the time

Same photos, same measurement method, at the near-lossless distance most
delivery workflows use:

| Source | Effort | Buffering | RAM per worker | Time | JXL size |
|---|---|---|---|---|---|
| 24 MP | 7 | default | 0.86 GB | 8.1 s | 19.4 % |
| 24 MP | 9 | default | 0.92 GB | 9.4 s | 19.4 % |
| 24 MP | 9 | `0` | 6.53 GB | 42.3 s | 20.6 % |
| 45 MP | 7 | default | 1.56 GB | 13.1 s | 17.4 % |
| 45 MP | 9 | default | 1.56 GB | 15.4 s | 17.4 % |
| 45 MP | 9 | `0` | 12.09 GB | 74.5 s | 18.7 % |

**RAM is the same as lossless**, so size `--workers` from either table. What
collapses is time: effort 9 goes from 79.9 s to 9.4 s at 24 MP and from 168.7 s
to 15.4 s at 45 MP (8–11× faster). Effort is nearly free here — 7 → 9 costs
about a second and changes nothing in the output.

> **Never combine `--buffering 0` with lossy.** At effort 9 it costs 7–8× the
> RAM and ~4.7× the time **and produces a larger file** (20.6 % vs 19.4 % at
> 24 MP; 18.7 % vs 17.4 % at 45 MP). It loses on all three axes. At 12 GB per
> worker, five workers would already need 60 GB. At effort 7 it is merely
> pointless: double the RAM for the same time and the same size.

### 8-bit sources: much faster, barely lighter

The table above is all **16-bit** input. Running the same photos converted to
8 bits (identical dimensions and ICC, pixels shifted down by 8):

| Source | Effort | RAM per worker | vs 16-bit | Time | vs 16-bit |
|---|---|---|---|---|---|
| 24 MP 8-bit | 7 | 0.69 GB | −21 % | 6.4 s | 3.1× faster |
| 24 MP 8-bit | 9 | 0.95 GB | −4 % | 13.2 s | 6.1× faster |
| 45 MP 8-bit | 7 | 1.40 GB | −19 % | 11.8 s | 3.1× faster |
| 45 MP 8-bit | 9 | 1.36 GB | −12 % | 24.7 s | 6.8× faster |

**Do not raise `--workers` for 8-bit work expecting the memory to halve.** Bit
depth is a speed lever, not a memory one: encoding is 3–7× faster, but RAM
falls only 4–21 %, because `cjxl` works in an internal representation that does
not shrink with input depth. Only the PNG intermediate halves. Size the worker
count from the 16-bit figures either way.

Every figure on this page is measured on **libjxl v0.12.0**. Older libjxl is
not characterised here — if you are on one, upgrading is the answer.

---

## Logs

```
<script_folder>/Logs/jxl_tiff_encoder/YYYYMMDD_HHMMSS.log
```

Opening line shows all active settings:
```
Mode: 7 | Effort: 7 | Distance: 0.1 | RAM PNG: False | D50: auto | Workers: 16
```

Final summary includes D50 patch statistics (when applicable):
```
Done: 42 OK | 0 overwrites | 0 skipped | 0 errors
D50 patch: 15 applied | 27 skipped (mode: auto)
```


---

## How to verify output

```powershell
# Check JXL has embedded ICC (ICC lives in XMP CreatorTool)
exiftool -XMP-xmp:CreatorTool photo.jxl | findstr "ICC:"

# Check encoding params (they live in dc:Description)
exiftool -XMP-dc:Description photo.jxl

# Check EXIF Software (for D50 patch detection)
exiftool -Software photo.jxl

# Full JXL info
jxlinfo -v photo.jxl
```


---

## Known behaviors 

### IrfanView and color-calibrated monitors (reported & fixed)

**Update:** This issue was reported to the IrfanView developer and an updated plugin DLL with proper ICC profile support was received. It is recommended to download the latest JXL plugin from the IrfanView website to test if the fix has been publicly released.

*Previous behavior (old plugin):
```
JXL lossless files embed the ICC color profile as a blob. Most software handles this
correctly — GIMP, XnView MP, Darktable, Firefox, Waterfox, and `jxl_to_jpeg.py` all
display correct colors.

IrfanView's behavior with lossless JXL appeared to depend on the system display profile
installed on the machine. The issue was specific to IrfanView on calibrated systems.

**The files themselves are correct.** Any conformant JXL decoder will display the colors
accurately. If using an old IrfanView plugin where colors look wrong, use lossy at `d=0.1` 
(imperceptible difference), or open the files in any of the other viewers listed above.
```

*For detailed technical information about JXL color management, XYB vs non-XYB, 
ICC blobs vs native primaries, and primary coordinates reference tables, 
see [JXL Color Internals](jxl_color_internals.md).*

### IrfanView EXIF display limitations

**TIFF → JXL (this tool):** EXIF is visible in IrfanView ✅  
*Reason: Boxes are reordered so Exif comes before codestream.*

**JPEG → JXL:** EXIF is **not visible** in IrfanView ❌  
*Reason: Uses Brotli compression (`brob` box) which IrfanView cannot read.*

**Recommendation:** Use **XnView MP** or **digiKam** for reliable EXIF viewing regardless of source format.

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

## Multi-Page TIFF Support (v1.7.0+)

Multi-page TIFFs are handled explicitly instead of silently discarding extra pages:

- `--multipage-mode split` — **default**. Encode each real page to a separate JXL
  (`photo.jxl`, `photo_page1.jxl`, ...). A TIFF with one real page yields exactly
  `photo.jxl`, identical to the old `ignore` default. Thumbnail pages follow
  `--thumbnail-mode` (`exclude` default, or `include`).
- `--multipage-mode ignore` — encode only page 0, drop the rest (the pre-v1.8.2
  default). Not silent: files whose extra pages are dropped are logged as warnings
  (capped at 20 lines per run) and the run summary repeats the total
  (`Multi-page: DISCARDED N page(s) from M TIFF(s)`). Many TIFFs are multi-page
  without looking like it — Capture One and most scanners append an embedded
  preview page, and film scanners add an IR/mask page.
- `--multipage-mode skip` — skip files that have more than one "real" page
- `--multipage-mode split_all` — encode every page, **always including thumbnails**.
  Equivalent to `--multipage-mode split --thumbnail-mode include`; `--thumbnail-mode`
  is ignored in this mode.

Thumbnail pages are detected via standard TIFF `SubfileType` flags (`is_reduced` / `is_subifd`). When splitting, thumbnails can be excluded or included with a configurable suffix (`--thumbnail-suffix`, default `_thumbnail`).

**Thumbnail exclusion is not warned per file.** `--thumbnail-mode exclude` is an
explicit request, so it produces a single summary line
(`Thumbnails: excluded N thumbnail page(s) from M TIFF(s)`) instead of one WARNING
per file — a library where every export carries a preview page would otherwise
bury the real errors. Pass `--warn-thumbnail-discard` when you want the file names.

Split pages carry a group marker in `XMP-dc:Relation` (`jxlphoto-mpg:<id>`), so `jxl_tiff_decoder.py` can reconstruct the original multi-page TIFF only from genuinely split files. Independently-named files such as `scan.jxl` + `scan_page2.jxl` are never merged.

Since v1.8.1, split pages also carry `jxlphoto-page:<N>` (the TIFF page index) and `jxlphoto-thumb` (thumbnail role) in `dc:Relation`. The decoder treats these as **authoritative** — the filename is only a fallback for JXLs encoded before v1.8.1. This makes reconstruction safe even when the source TIFF itself is named like a page (`scan_page3.tif`, `holiday_thumbnail.tif`).

### Per-Page ICC Preservation (v1.7.0)

When splitting, each page is encoded with its own effective ICC profile (read from the page's own ICC tag 34675; if absent, page N > 0 inherits IFD0's profile for color interpretation). Pages that inherit the ICC are flagged with `jxlphoto-icc:inherited` in `dc:Relation`, so the decoder can reconstruct them without an ICC tag when the original page also had none.

### Grayscale and SubfileType Preservation (v1.7.0)

Single-channel pages are encoded as grayscale and flagged with `jxlphoto-grayscale` in `dc:Relation`. The original `SubfileType` value (e.g. `2` for PAGE, `4` for MASK) is also recorded so the decoder can restore the page's role. Inherited RGB ICC is not applied to grayscale pages, which prevents libpng iCCP errors on scanner IR/mask pages.

> **⚠️ IR channel / Digital ICE warning:** If your scanner software (e.g. SilverFast, VueScan) uses the IR page as a hidden channel for Digital ICE / dust & scratch removal, converting the TIFF to JXL and back may break that feature. Those programs often rely on vendor-specific tags and exact page ordering beyond the standard TIFF `SubfileType`. This tool preserves the page as a standard grayscale `PAGE`, but the original scanner software may no longer recognize it as an IR mask. Test with one file before batch-processing important film scans.

### Examples

```bash
# Old behavior: encode only page 0, ignore extra pages
# (mode 8 will then REFUSE to delete any source whose pages were dropped)
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode ignore

# Skip any TIFF that has more than one real page
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode skip

# Split pages and exclude thumbnails
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode split --thumbnail-mode exclude

# Split every page, including thumbnails, with a custom suffix
# (split_all already includes thumbnails; --thumbnail-mode has no effect here)
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode split_all --thumbnail-suffix "_thumb"

# Split, drop thumbnails, but list every file whose thumbnail was dropped
python jxl_tiff_encoder.py "E:\photos" "E:\photos_jxl" --mode 2 --multipage-mode split --thumbnail-mode exclude --warn-thumbnail-discard

# Film scanner workflow: encode main + preview + IR/mask pages
python jxl_tiff_encoder.py "E:\film_scans" "E:\film_scans_jxl" --mode 2 --multipage-mode split_all --thumbnail-mode include
```

---

## Disclaimer

These tools were made for my personal workflow. 
Use at your own risk — I am not responsible for any issues you may encounter.

However, If you find any bugs, feel free to report to me - I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.

---

## Changes since v1.0

### New Features

**PIL decompression bomb limit — configurable for large panoramas**
Added `PIL_MAX_IMAGE_PIXELS` setting to disable or configure PIL's decompression bomb protection. This prevents false "DOS attack" warnings when processing large panoramas (100+ MP) that exceed PIL's default ~89MP limit.

- `None` (default): Disable the limit completely (recommended for trusted local files)
- `N`: Set custom pixel limit (e.g., `500_000_000` for ~500MP)

**D50 illuminant patch — auto-detection (default)**
Capture One **may export** files with a known ICC rounding error that causes cjxl warnings. The patch was already part of the toolkit, but now supports three operating modes:

- `auto` (default): Only applies D50 patch when EXIF `Software` field contains `capture one` or `captureone` — other files are unaffected.
- `on`: Always applies the D50 patch to all files (forces correction regardless of source software).
- `off`: Never applies the D50 patch (uses original ICC values as-is).

CLI flag: `--d50-patch auto|on|off`
Script setting: `D50_PATCH_MODE = "auto"` (default)

### Bug Fixes
- Integer overflow in JXL box parser (size validation added)
- Race condition in staging directory (UUID-based filenames)
- D50 patch statistics now shown in summary output

Full tracking: [bug_tracking_since_v1.0.md](./bug_tracking_since_v1.0.md) | [new_features_since_v1.0.md](./new_features_since_v1.0.md) | [code_quality_refactoring.md](./code_quality_refactoring.md)

---

## License

MIT License — feel free to use, modify, and distribute.

---

## Acknowledgments

- [libjxl](https://github.com/libjxl/libjxl) team for JPEG XL implementation  
- [ExifTool](https://exiftool.org) by Phil Harvey for metadata handling  
- [tifffile](https://github.com/cgohlke/tifffile) by Christoph Gohlke for TIFF I/O  
- [Kimi](https://www.kimi.com) (Moonshot AI) and [Claude](https://www.anthropic.com/claude) (Anthropic) for code assistance and technical discussion
