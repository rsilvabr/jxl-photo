# jxl_jpeg_transcoder.py

Unified JPEG XL toolkit combining **lossless JPEG↔JXL transcoding** with **lossy conversion and ICC color management**.

Intelligently auto-detects the operation needed based on file type:

- **JPEG input** → Lossless transcoding to JXL (~20% smaller, fully reversible)
- **JXL with jbrd box** → Lossless recovery of original JPEG (bit-perfect)
- **JXL without jbrd** → Lossy decode to JPEG/PNG with optional ICC conversion
- **PNG input** → Convert to JXL (lossy or modular lossless)

This eliminates the need to remember multiple scripts or command structures — one tool handles archival transcoding, delivery conversion, and round-trip workflows.

* * *

## Requirements

```
Python 3.10+
cjxl / djxl → https://github.com/libjxl/libjxl/releases
exiftool → https://exiftool.org
magick → https://imagemagick.org (optional, for ICC conversion only)
```

Both `cjxl.exe`, `djxl.exe`, and `exiftool.exe` must be on your PATH. ImageMagick is only required if using `--icc-profile`.

Quick way to add them (PowerShell, then reopen terminal):

```powershell
$p = [Environment]::GetEnvironmentVariable("PATH", "User")
[Environment]::SetEnvironmentVariable("PATH", "$p;C:\tools\libjxl\bin;C:\tools\exiftool;C:\Program Files\ImageMagick-7.1.1-Q16-HDRI", "User")
```

Verify:

```
cjxl --version  # JPEG XL encoder v0.11.x
djxl --version  # JPEG XL decoder v0.11.x
exiftool -ver   # 13.xx
magick -version # ImageMagick 7.x (optional)
```

* * *

## Quick start

```powershell
# ── Auto-detection — no flags needed for basic operations ────────

# JPEG → JXL (lossless transcoding, in-place)
py jxl_jpeg_transcoder.py "F:\Photos\photo.jpg"

# JXL → JPEG (auto-detects: lossless recovery if jbrd present, else lossy)
py jxl_jpeg_transcoder.py "F:\Photos\photo.jxl"

# JXL → PNG 16-bit (preserves full bit depth, archival quality)
py jxl_jpeg_transcoder.py "F:\Photos\photo.jxl" --format png

# JXL → sRGB JPEG (color space conversion using ImageMagick built-in)
py jxl_jpeg_transcoder.py "F:\Photos\photo.jxl" --to-srgb --quality 95

# PNG → JXL (convert to JXL format)
py jxl_jpeg_transcoder.py "F:\Photos\photo.png"

# ── Smart sync — only process if source is newer ─────────────────
py jxl_jpeg_transcoder.py "F:\Photos" --mode 8 --sync

# ── Batch operations with modes ───────────────────────────────────

# Mode 8: Recursive in-place transcoding (JPEG ↔ JXL)
py jxl_jpeg_transcoder.py "F:\2024\Export_JPEG" --mode 8

# Mode 1: Output to subfolder (converted_jxl/ or recovered_jpeg/)
py jxl_jpeg_transcoder.py "F:\2024\Export_JPEG" --mode 1

# Force convert even if jbrd present (e.g., for web delivery with ICC change)
py jxl_jpeg_transcoder.py "F:\2024\JXL_ProPhoto" --force-convert --to-srgb

# Convert with external ICC profile (professional print workflow)
py jxl_jpeg_transcoder.py "F:\2024\JXL_Archive" --icc-profile "C:\ICC\AdobeRGB1998.icc"

# 16 parallel workers for batch processing
py jxl_jpeg_transcoder.py "F:\2024" --mode 8 --workers 16
```

* * *

## Key settings

Edit at the top of the script:

```python
# ── Transcode settings (lossless JPEG ↔ JXL) ─────────────────────
CJXL_EFFORT = 7
# Compression effort (1-10). Controls file size, NOT quality.
# 7 is the sweet spot for camera photos. Effort 9-10 is much slower
# for marginal gains on JPEG transcoding.

STORE_MD5 = True
# True → after encoding, append source JPEG MD5 to checksums.md5
# database. Used for bit-perfect verification on decode.
# False → no MD5 stored; decode cannot verify integrity.

DELETE_SOURCE = False
# [Mode 8 only] Delete source JPEG after successful encode.
# WARNING: irreversible. Only enable after testing on a small batch.
# Requires confirmation (DELETE_CONFIRM = True) unless disabled.
# 
# SAFETY: Lossy operations require HHMM confirmation (see below).

# ── Convert settings (lossy JXL → JPEG/PNG) ───────────────────────
JPEG_QUALITY = 95
# 1-100. 95 = high quality archival. Ignored for PNG output.

PNG_BIT_DEPTH = 16
# 8 or 16. Default 16-bit for PNG preserves full tonal range.
# Automatically switches to PNG if 16-bit requested with JPEG format.

# ── Paths ─────────────────────────────────────────────────────────
TEMP2_DIR = None
# Staging directory for output files during conversion.
# Example: r"E:\staging_jxl"
# None → write directly to final destination.
# Useful to separate read I/O (HDD with source) from write I/O.

# ── Behavior ─────────────────────────────────────────────────────
OVERWRITE = "smart"
# False → skip if output exists (safe for resuming)
# True → always overwrite
# "smart" → only overwrite if source is newer than destination (default)
```

#### Safety confirmation (mode 8 + DELETE_SOURCE)

**Lossless transcoding (JPEG ↔ JXL):**
```
⚠ WARNING — DELETE_SOURCE is enabled
Source files will be deleted after successful operation.
This is IRREVERSIBLE. Type 'yes' to confirm.

> yes
Confirmed.
```

**Lossy conversion (JXL → JPEG/PNG, or PNG → JXL):**
```
⚠️  WARNING — DELETE_SOURCE is enabled for LOSSY conversion
Source files will be PERMANENTLY DELETED after conversion.
This operation involves LOSSY compression and is IRREVERSIBLE.

Type the current time (HHMM) to confirm you understand the risks.
(Any other input will cancel)

> HHMM
Confirmed.
```

* * *

## How auto-detection works

The script analyzes the input file extension and content to determine the optimal operation:

| Input | Detection | Operation | Output |
| --- | --- | --- | --- |
| `.jpg` / `.jpeg` | File extension | **TRANSCODE** encode | JXL (lossless, ~20% smaller) |
| `.jxl` with `jbrd` box | Box scan (16KB header) | **TRANSCODE** decode | Original JPEG (bit-perfect recovery) |
| `.jxl` without `jbrd` | Box scan | **CONVERT** decode | New JPEG/PNG (lossy recompression) |
| `.png` | File extension | **CONVERT** encode | JXL (modular or VarDCT) |

**Override flags** (when auto-detect isn't what you need):

- `--force-transcode` → Force lossless transcoding (will fail if jbrd missing on decode)
- `--force-convert` → Force lossy conversion (e.g., to apply ICC profile to lossless JXL)
- `--decode` → Force decode direction for JXL files

* * *

## ⚠️ Modes 6 and 7 — ONLY files inside `_EXPORT`

**Modes 6 and 7 ONLY process files inside folders containing `_EXPORT`. Everything outside is IGNORED.**

```
E:\sessao\
├── foto1.jpg          ← NOT processed (outside _EXPORT)
├── foto2.jpg          ← NOT processed (outside _EXPORT)
└── _EXPORT\
    ├── folder1\
    │   └── img.jpg    ← PROCESSED ✓
    ├── folder2\
    │   └── img.jpg    ← PROCESSED ✓
    └── folder3\sub\
        └── img.jpg    ← PROCESSED ✓
```

**Mode 6** — processes ALL files under ALL `_EXPORT` folders found recursively.

**Mode 7** — like mode 6, but only files inside a SPECIFIC subfolder of `_EXPORT`.
Default for encode (JPEG→JXL): `_EXPORT/JPEG_recovered`
Default for decode (JXL→JPEG): `_EXPORT/JPEG_recovered`

```
Mode 7 example (encode):
session/_EXPORT/JPEG_recovered/photo.jpg → session/_EXPORT/JXL_jpeg/photo.jxl  ✓
session/_EXPORT/sRGB/photo.jpg          → ignored (doesn't match subfolder)

Mode 7 example (decode):
session/_EXPORT/JXL/photo.jxl      → session/_EXPORT/JPEG_recovered/photo.jpg  ✓
session/_EXPORT/AdobeRGB/photo.jxl → ignored
```

## Output modes

Modes 0-8 mirror the original `jxl_jpg_lossless_transcoder.py` structure:

### Encode modes (JPEG → JXL)

| Mode | Input | Output location | Example |
| --- | --- | --- | --- |
| `0` | File or folder | In-place (flat, non-recursive) | `photo.jxl` (same folder) |
| `1` | Single file | `converted_jxl/` subfolder | `.../converted_jxl/photo.jxl` |
| `2` | Directory | Recursive to output_root | `output_root/photo.jxl` |
| `3` | Directory | `converted_jxl/` inside each folder | `.../JPEG/converted_jxl/photo.jxl` |
| `4` | Directory | Sibling folder `JXL_jpeg/` | `.../JXL_jpeg/photo.jxl` |
| `5` | Directory | Rename folder (JPEG→JXL) | `.../Export_JXL/photo.jxl` |
| `6` | Directory | ONLY files INSIDE `_EXPORT` — ignores everything outside | `.../session/_EXPORT/JXL_jpeg/...` |
| `7` | Directory | Like mode 6 but only specific `_EXPORT` subfolder | `.../session/_EXPORT/JXL_jpeg/...` |
| `8` | Directory | In-place recursive | `.../session/photo.jxl` (next to each JPEG) |

### Decode modes (JXL → JPEG)

| Mode | Input | Output location | Example |
| --- | --- | --- | --- |
| `0` | Single file | In-place | `photo.jpg` (same folder) |
| `1` | Single file | `recovered_jpeg/` subfolder | `.../recovered_jpeg/photo.jpg` |
| `2` | Directory | Recursive to output_root | `output_root/photo.jpg` |
| `3` | Directory | `recovered_jpeg/` inside each folder | `.../JXL/recovered_jpeg/photo.jpg` |
| `4` | Directory | Sibling folder `JPEG_recovered/` | `.../JPEG_recovered/photo.jpg` |
| `5` | Directory | Rename folder (JXL→JPEG_recovered) | `.../Export_JPEG_recovered/photo.jpg` |
| `6-7` | Directory | ONLY files INSIDE `_EXPORT` — ignores everything outside | `.../_EXPORT/JPEG_recovered/...` |
| `8` | Directory | In-place recursive | `.../session/photo.jpg` (next to each JXL) |

**Note:** Mode 8 with `--delete-source` is the "archive and replace" workflow — verify your setup with a small batch first. Remember: **lossy operations require HHMM confirmation**, while lossless only requires "yes".

* * *

## CLI reference

```
py jxl_jpeg_transcoder.py <input> [options]

Arguments:
  input              Input file or folder (JXL, JPEG, or PNG)

Options:
  --mode 0-8         Output folder mode (default: 0 for in-place)
  --workers N        Parallel threads (tested up to 32 on a Ryzen 9 5950X)
  --overwrite        Always overwrite existing files
  --sync             Smart mode: only process if source is newer than destination
                     (checks file modification time: src.stat().st_mtime > dst.stat().st_mtime)

  --format jpeg|png  Output format for JXL decode (default: jpeg)
                     PNG defaults to 16-bit depth
  --quality 1-100    JPEG quality (default: 95)
  --bit-depth 8|16   Output bit depth (PNG only, default: 16)

  --icc-profile PATH Path to ICC profile for color conversion.
                     Can be a file path (e.g., "C:\icc\AdobeRGB.icc") or a
                     built-in name: "sRGB", "Adobe RGB", "ProPhoto RGB"
  --to-srgb          Shortcut: convert to sRGB using ImageMagick built-in
                     (shorthand for --icc-profile sRGB)

  --force-transcode  Override auto-detect, force lossless transcoding
  --force-convert    Override auto-detect, force lossy conversion
  --decode           Force decode direction for JXL files

  --no-md5           Skip MD5 storage (encode only)
  --no-verify        Skip MD5 verification (decode only)
  --delete-source    Delete source after mode 8 encode (requires confirmation)

  --staging PATH     Staging directory for output files
  --effort 1-10      cjxl effort (default: 7)
  --dry-run          Preview operations without converting
```

* * *

## Smart Sync Mode (`--sync`)

The `--sync` flag enables "smart" overwrite behavior that only re-processes files when the source is newer than the existing output:

```powershell
py jxl_jpeg_transcoder.py "F:\Photos" --mode 8 --sync
```

**Logic:**
- If output doesn't exist → process normally
- If output exists and source is newer (by modification time) → overwrite
- If output exists and source is older or same age → skip

**Use cases:**
- Resuming interrupted batch operations without re-processing completed files
- Update archives when source files are modified
- Incremental backups

**Comparison with `--overwrite`:**

| Flag | Behavior |
| --- | --- |
| (none) | Skip existing files (safest for initial runs) |
| `--overwrite` | Always overwrite existing files |
| `--sync` | Only overwrite if source is newer than destination |

* * *

## ICC Color Management

### Two approaches for JXL → JPEG/PNG

**1. Preserving original ICC (default)**

```powershell
py jxl_jpeg_transcoder.py photo.jxl
# ICC: preserving embedded from JXL
# Output has same color space as input (ProPhoto, AdobeRGB, etc.)
```

**2. Converting to sRGB (web/standard delivery)**

```powershell
# Using ImageMagick built-in (fast, no file needed):
py jxl_jpeg_transcoder.py photo.jxl --to-srgb

# Using specific ICC file (professional accuracy):
py jxl_jpeg_transcoder.py photo.jxl --icc-profile "C:\ICC\sRGB.icc"
```

### When to use which

| Scenario | Command | ICC handling |
| --- | --- | --- |
| Archival recovery | `--force-transcode` (if jbrd) | Original preserved |
| Web delivery | `--to-srgb` | Convert to sRGB |
| Print workflow | `--icc-profile AdobeRGB.icc` | Convert to target profile |
| Client delivery (generic) | No ICC flags | Preserve original |

**Important:** `--to-srgb` uses ImageMagick's built-in sRGB color space (mathematical approximation). For critical color work, use `--icc-profile` with a specific ICC file.

* * *

## MD5 Verification (Transcode only)

For lossless transcoding (JPEG → JXL → JPEG), the script maintains a `checksums.md5` database in each output folder:

```
53b75f86f7c1042a38776eda47654fce  _DSC4550.jxl
a3f1c2d8e9b047f6123456789abcdef0  _DSC4551.jxl
```

**During encode:** MD5 of source JPEG is appended to database.

**During decode:** Recovered JPEG MD5 is compared against stored hash.

Log output:

```
[14:23:01] | INFO | [42/90] OK ✓ MD5 PASS | DSC_0042.jxl
[14:23:01] | ERROR | [43/90] MD5 FAIL | DSC_0043.jxl
[14:23:01] | WARNING | [44/90] OK (no MD5 stored) | DSC_0044.jxl
```

**Note:** MD5 verification only applies to lossless transcoding. Lossy conversion (`--force-convert`) intentionally changes pixel data, so MD5 verification is skipped.

* * *

## Technical Details

### jbrd Detection (JPEG Bitstream Reconstruction Data)

The script scans the first **16KB** of JXL files to detect the `jbrd` box, which indicates the file can be losslessly transcoded back to the original JPEG. 

- **16KB coverage**: Handles 99.9% of real-world files, including those with large Exif/XMP metadata headers
- **False negatives**: Extremely rare; if missed, file falls back to lossy convert (safe degradation)
- **False positives**: Impossible (exact byte signature matching)

If you encounter a valid JPEG-reconstructible JXL that isn't detected, the file likely has its `jbrd` box positioned >16KB into the file (highly unusual). Use `--force-transcode` as workaround.

* * *

## Round-trip workflow examples

### Scenario 1: Archive JPEGs with future recovery

```powershell
# Encode: JPEG → JXL (lossless, smaller, with MD5)
py jxl_jpeg_transcoder.py "F:\Photos\2024" --mode 8

# [Future] Decode: JXL → JPEG (bit-perfect recovery)
py jxl_jpeg_transcoder.py "F:\Photos\2024" --decode --mode 8
```

### Scenario 2: Mixed archive (JPEGs + downloaded JXLs)

```powershell
# Folder has camera JPEGs and downloaded JXLs (no jbrd)
# Auto-detect handles each correctly:
py jxl_jpeg_transcoder.py "F:\Mixed" --mode 0
# JPEGs → transcoded to JXL
# JXLs without jbrd → converted to JPEG (lossy, preserved ICC)
```

### Scenario 3: Web delivery from JXL masters

```powershell
# Master JXLs in ProPhoto RGB
# Deliver sRGB JPEGs for web:
py jxl_jpeg_transcoder.py "F:\Masters\JXL" --force-convert --to-srgb --quality 95 --mode 1
# Output: converted/ folder with sRGB JPEGs
```

* * *

## Performance

| Setting | Recommendation |
| --- | --- |
| Workers | Match CPU thread count (tested up to 32 on a Ryzen 9 5950X) |
| Staging | Set `TEMP2_DIR` to SSD when source is on HDD — avoids I/O contention |
| RAM mode | Not applicable (no PNG intermediate for JPEG transcoding) |
| Effort | Keep at 7 for JPEG transcoding; higher efforts yield marginal gains |

* * *

## Logs

```
/Logs/jxl_jpeg_transcoder/YYYYMMDD_HHMMSS.log
```

Opening line shows detected operation and active settings:

```
[14:23:01] | INFO | TRANSCODE lossless | Mode: 8 | Effort: 7 | Store MD5: True | delete_source=False | overwrite=ON | Staging: disabled | Workers: 16
[14:23:01] | INFO | TRANSCODE lossless | Mode: 8 | Effort: 7 | Store MD5: True | delete_source=False | smart (source newer → overwrite) | Staging: disabled | Workers: 16
[14:23:01] | INFO | CONVERT lossy | Mode: 0 | Format: jpeg | ICC: converting to ImageMagick sRGB (built-in) | overwrite=OFF (skip existing)
```

File progress examples:

```
[14:23:01] | INFO | [1/100] OK | photo.jpg → photo.jxl
[14:23:01] | INFO | [2/100] OVERWRITE | photo.jpg → photo.jxl
[14:23:01] | INFO | [3/100] SKIP (exists) | photo.jpg
[14:23:01] | INFO | [4/100] SKIP (destination newer) | photo.jpg  # --sync mode only
[14:23:01] | INFO | [5/100] OK ✓ MD5 PASS | photo.jxl
```

* * *

## Troubleshooting

### "SKIP (exists)" when I want to overwrite

Use `--overwrite` to force overwriting existing files, or `--sync` to only overwrite if source is newer.

### "SKIP (destination newer)" in --sync mode

This means the existing output file has a more recent modification time than the source. The script assumes the output is up-to-date and skips processing. Use `--overwrite` to force processing regardless of timestamps.

### "MD5 FAIL" on decode

The JXL was modified after encoding, or was not encoded with `--lossless_jpeg=1`. Files encoded by other tools (Lightroom, etc.) won't pass MD5 verification.

### "No EXIF found in output" warning

Some JPEGs lack EXIF or use non-standard formats. EXIF is optional for viewing but may affect metadata workflows.

### IrfanView shows wrong colors

IrfanView's JXL support depends on system display profile. The files are correct — use XnView MP, GIMP, or browsers for verification. If critical, use lossy decode (`--force-convert`) which generates standard JPEG ICC.

* * *

## Relationship with other scripts

| Script | Purpose | This script replaces... |
| --- | --- | --- |
| `jxl_jpeg_transcoder.py` | **This tool** — Unified JPEG/JXL/PNG with auto-detect | `jxl_jpg_lossless_transcoder.py` + `jxl_to_jpg_png.py` |
| `jxl_tiff_encoder.py` | 16-bit TIFF → JXL | Not replaced — use for Capture One 16-bit TIFFs |
| `jxl_tiff_decoder.py` | JXL → 16-bit TIFF with ICC embedding | Not replaced — companion to jxl_tiff_encoder |

**Migration from `jxl_jpg_lossless_transcoder.py`:**

- All modes (0-8) work identically
- MD5 database format is the same (checksums.md5)
- Additional: Auto-detect, PNG input, ICC conversion, lossy decode, `--sync` support
- Safety: Different confirmation levels for lossy vs lossless delete

* * *

## Disclaimer

These tools were made for my personal workflow. Use at your own risk — I am not responsible for any issues you may encounter.

However, if you find any bugs, feel free to report to me — I will gladly try my best to improve this project.

Always test with a small batch before processing important archives.

* * *

## License

MIT License — feel free to use, modify, and distribute.

* * *

## Acknowledgments

- libjxl team for JPEG XL implementation
- ExifTool by Phil Harvey for metadata handling
- [MiniMax](https://www.minimax.io/) (MiniMax AI) and [Kimi](https://www.kimi.com) (Moonshot AI) for code assistance and technical discussion
