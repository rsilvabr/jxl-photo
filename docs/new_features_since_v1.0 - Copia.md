# New Features Since v1.0

Date: 2026-04-04
Scripts: `jxl_photo.py`, `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

---

## Summary Table

| # | Feature | Scripts | Status |
|---|---------|---------|--------|
| 1 | D50 Illuminant Patch | encoder | ✅ ADDED |
| 2 | Metadata Strip Mode | encoder | ✅ ADDED |
| 3 | Encode Tag Location | encoder | ✅ ADDED |
| 4 | Smart Sync Mode | all | ✅ ADDED |
| 5 | Staging Directory Support | all | ✅ ADDED |
| 6 | Interactive Wizard (jxl_photo.py) | photo | ✅ ADDED |
| 7 | Mode 8 (Delete Source) | all | ✅ ADDED |
| 8 | ICC Matrix Conversion | decoder | ✅ ADDED |
| 9 | Roundtrip ICC Preservation | encoder, decoder | ✅ ADDED |
| 10 | JPEG Transcoding (jbrd detection) | transcoder | ✅ ADDED |
| 11 | Built-in Color Space Support | transcoder | ✅ ADDED |
| 12 | PPM Validation | decoder | ✅ ADDED |
| 13 | Configurable Export Marker | all | ✅ ADDED |
| 14 | D50 Count in OFF Mode | encoder | ✅ ADDED |

---

## Detailed Feature Descriptions

### Feature #1 — D50 Illuminant Patch (TIFF→JXL)

**Location:** `jxl_tiff_encoder.py`

**Description:** Automatically patches ICC profiles to use D50 illuminant when encoding TIFFs from Capture One. This fixes cjxl warnings about "ICC profile is not standard XYZ" caused by Capture One's incorrect D65 illuminant in v4 ICC profiles.

**Modes:**
- `auto` (default): Detects Capture One exports via EXIF Software field and applies patch only when needed
- `on`: Always apply D50 patch
- `off`: Never apply D50 patch

**CLI Usage:**
```bash
python jxl_tiff_encoder.py folder/ --d50-patch auto
python jxl_tiff_encoder.py folder/ --d50-patch on
python jxl_tiff_encoder.py folder/ --d50-patch off
```

**Wizard:** Step 6 (Basic Parameters) asks for D50 patch mode when TIFF→JXL.

**Statistics:** Conversion summary shows count of applied/skipped patches.

---

### Feature #2 — Metadata Strip Mode

**Location:** `jxl_tiff_encoder.py`

**Description:** Option to strip all metadata (EXIF, XMP) from output JXL files. Only encoding parameters are preserved in `dc:Description`.

**Use Cases:**
- Privacy: Remove GPS, camera info, timestamps
- Minimal file size: Strip all metadata for smallest possible JXL
- Clean archives: Only keep essential encoding info

**CLI Usage:**
```bash
python jxl_tiff_encoder.py folder/ --strip
```

**Wizard:** Step 6A (Advanced Options) → "Strip metadata?"

**Behavior:**
- When `--strip` is used: Only `dc:Description` with encoding params is kept
- Normal mode: Full EXIF/XMP preservation including ICC in XMP

---

### Feature #3 — Encode Tag Location

**Location:** `jxl_tiff_encoder.py`

**Description:** Configurable location for encoding parameters metadata. Choose where cjxl parameters (distance, effort) are recorded in the output JXL.

**Options:**
- `xmp` (default): Store in `xmp:CreatorTool` with "ICC:" prefix
- `software`: Store in EXIF Software field
- `off`: Don't record encoding parameters

**CLI Usage:**
```bash
python jxl_tiff_encoder.py folder/ --encode-tag xmp
python jxl_tiff_encoder.py folder/ --encode-tag software
python jxl_tiff_encoder.py folder/ --encode-tag off
```

**Wizard:** Step 6A (Advanced Options) → "Encode tag location"

---

### Feature #4 — Smart Sync Mode

**Location:** All scripts

**Description:** Reconvert only if source file is newer than destination. Prevents unnecessary re-processing of unchanged files during batch operations.

**Comparison:**
| Mode | Behavior |
|------|----------|
| Default (no flags) | Skip existing files |
| `--overwrite` | Always reconvert |
| `--sync` | Reconvert only if source newer than destination |

**CLI Usage:**
```bash
python jxl_tiff_encoder.py folder/ --sync
python jxl_tiff_decoder.py folder/ --sync
python jxl_jpeg_transcoder.py folder/ --sync
```

**Wizard:** Step 6 (Basic Parameters) asks "Existing file handling: [0] skip | [1] overwrite all | [2] sync"

---

### Feature #5 — Staging Directory Support

**Location:** All scripts

**Description:** Write output files to a staging directory first, then move to final destination in bulk. Reduces HDD seek contention and improves performance on slow drives.

**Benefits:**
- Faster batch processing on HDDs
- Reduced fragmentation
- Cleaner output (atomic move operation)

**CLI Usage:**
```bash
python jxl_tiff_encoder.py folder/ --staging "E:\staging"
python jxl_tiff_decoder.py folder/ --staging "E:\staging"
python jxl_jpeg_transcoder.py folder/ --staging "E:\staging"
```

**Configuration:**
```python
TEMP2_DIR = None  # Default: disabled (write directly to destination)
TEMP2_DIR = r"E:\staging_jxl"  # Enable staging
```

**Notes:**
- Staging files use UUID prefix to avoid collisions
- Files are moved atomically after conversion completes
- Orphaned files in staging are NOT auto-deleted (safety)

---

### Feature #6 — Interactive Wizard (jxl_photo.py)

**Location:** `jxl_photo.py`

**Description:** Menu-driven interface for users who prefer not to use command-line arguments. Guides through conversion setup step-by-step.

**Main Menu Options:**
1. **Convert folder** — Run wizard for new conversion
2. **Repeat last workflow** — Reuse previous settings with new folder
3. **Check dependencies** — Verify cjxl/djxl/exiftool/ImageMagick
4. **Edit default settings** — Configure defaults (quality, effort, markers)
5. **Erase all settings** — Reset to factory defaults
6. **Move settings file** — Toggle between USERPROFILE and script folder

**Wizard Steps:**
1. Select source format (JPEG/TIFF/JXL)
2. Select destination format
3. Choose files/folder
4. Select organization mode (0-8)
5. Mode-specific configuration
6. Basic parameters (quality, workers, staging)
7. Advanced options (optional)
8. Expert mode (optional)
9. Confirmation and execution

**Features:**
- Rich UI (colors, tables, prompts) when `rich` library available
- Fallback to plain text when `rich` not installed
- Settings persistence between sessions
- Session history for repeat workflows

---

### Feature #7 — Mode 8 (Delete Source)

**Location:** All scripts

**Description:** Mode 8 is "In-place recursive + delete source option". After successful conversion, source files can be automatically deleted.

**Safety Features:**
- Only available in Mode 8 (not other modes)
- Requires `--delete-source` flag (not default)
- Confirmation required (time-based for lossy ops)
- MD5 verification for transcoded JPEGs before deletion

**CLI Usage:**
```bash
python jxl_tiff_encoder.py folder/ --mode 8 --delete-source
python jxl_tiff_decoder.py folder/ --mode 8 --delete-source
python jxl_jpeg_transcoder.py folder/ --mode 8 --delete-source
```

**Confirmation Types:**
- Lossless transcode: Type "yes" to confirm
- Lossy conversion: Type current time (HHMM) to confirm

---

### Feature #8 — ICC Matrix Conversion

**Location:** `jxl_tiff_decoder.py`

**Description:** Alternative decode mode using linear RGB conversion with LittleCMS ICC transforms. Useful when standard djxl color management produces incorrect colors.

**Decode Modes:**
1. **Roundtrip** (default with ICC): Standard djxl + original ICC attachment
2. **Basic**: djxl auto only, no color management
3. **Matrix** (`--matrix`): Linear decode + LittleCMS transform

**CLI Usage:**
```bash
python jxl_tiff_decoder.py folder/ --matrix
```

**Wizard:** Step 6A (Advanced Options) → "Use ICC matrix conversion?"

**Requirements:** `PIL.ImageCms` module (install with `pip install Pillow --upgrade`)

---

### Feature #9 — Roundtrip ICC Preservation

**Location:** `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`

**Description:** Full ICC profile preservation during TIFF↔JXL roundtrip. The original ICC profile is embedded in XMP metadata during encoding and restored during decoding.

**Encoding (TIFF→JXL):**
1. Extract ICC from TIFF
2. Patch D50 if needed (Feature #1)
3. Store original ICC in XMP (`xmp:CreatorTool` with ICC: prefix)
4. Use patched ICC for PNG intermediate

**Decoding (JXL→TIFF):**
1. Extract ICC from XMP metadata
2. Decode to PPM with sRGB
3. Convert PPM→TIFF with original ICC attached
4. Cleanup XMP ICC marker (optional)

**Result:** Output TIFF has identical color profile to input TIFF.

---

### Feature #10 — JPEG Transcoding (jbrd Detection)

**Location:** `jxl_jpeg_transcoder.py`

**Description:** Automatic detection of JPEG bitstream reconstruction data (jbrd box) in JXL files. Enables lossless recovery of original JPEG when available.

**Auto-Detection:**
- JPEG input → Always transcode encode (lossless to JXL)
- JXL with jbrd → Transcode decode (lossless JPEG recovery)
- JXL without jbrd → Convert (lossy to JPEG/PNG)
- PNG input → Convert to JXL (no lossless transcode for PNG)

**CLI Usage:**
```bash
# Auto-detect based on input
python jxl_jpeg_transcoder.py photo.jpg
python jxl_jpeg_transcoder.py photo.jxl

# Force specific mode
python jxl_jpeg_transcoder.py photo.jxl --force-transcode
python jxl_jpeg_transcoder.py photo.jpg --force-convert
```

---

### Feature #11 — Built-in Color Space Support

**Location:** `jxl_jpeg_transcoder.py`

**Description:** Support for built-in color spaces (sRGB, Adobe RGB, ProPhoto) in addition to ICC profile files.

**Color Spaces:**
- `sRGB` — Standard web color space
- `Adobe RGB` — Wide gamut for photography
- `ProPhoto RGB` — Ultra-wide gamut

**CLI Usage:**
```bash
python jxl_jpeg_transcoder.py photo.jxl --icc-profile sRGB
python jxl_jpeg_transcoder.py photo.jxl --to-srgb  # Shortcut
```

**Wizard:** Step 6 (Basic Parameters) → "Convert to sRGB?"

**Technical:** Uses ImageMagick `-colorspace` instead of `-profile` for built-in spaces.

---

### Feature #12 — PPM Validation

**Location:** `jxl_tiff_decoder.py`

**Description:** Validation of PPM intermediate files to detect truncated or corrupted output from djxl.

**Checks:**
- File size matches expected dimensions
- No buffer overflow during read
- Graceful handling of truncated files

**Error Handling:**
```python
expected_size = height * width * 3 * (2 if maxval > 255 else 1)
if len(raw) < expected_size:
    raise RuntimeError(f"PPM file truncated: got {len(raw)} bytes, expected {expected_size}")
```

**Benefit:** Prevents cryptic NumPy reshape errors and corrupted TIFF output.

---

### Feature #13 — Configurable Export Marker

**Location:** All scripts

**Description:** Configurable folder marker for Modes 6 and 7. Default is `_EXPORT` but can be customized.

**Modes:**
- **Mode 6**: Process all files inside folders containing the marker
- **Mode 7**: Process files inside specific subfolder of marker (e.g., `_EXPORT/JXL`)

**Configuration:**
```python
EXPORT_MARKER = "_EXPORT"  # Default
EXPORT_JXL_SUBFOLDER = "JXL"  # For mode 7
```

**Wizard:** Step 5 asks for marker configuration when Modes 6/7 selected.

**Settings:** Saved in config file for persistence.

---

### Feature #14 — D50 Count in OFF Mode

**Location:** `jxl_tiff_encoder.py`

**Description:** When D50_PATCH_MODE="off", the script now tracks correctness even though no patching is applied. Users can see how many files were already correct vs would have needed patching, helping them decide if they should enable patching.

**Behavior:**

| D50_PATCH_MODE | Applied | Already Correct | Would Have Needed |
|----------------|---------|-----------------|-------------------|
| `auto` | Yes (when triggered) | Yes | Implicit (applied - already_correct) |
| `on` | Yes (always) | Yes | Implicit |
| `off` | No | Yes (tracked) | Yes (tracked) |

**Summary Output:**
```
# mode: off — shows what would have happened
D50 patch: 2 already correct | 8 would have needed (mode: off)

# mode: auto/on — shows what did happen
D50 patch: 15 applied (3 needed, 12 already correct) | 5 skipped (mode: auto)
```

**Benefit:** Users with D50_PATCH_MODE="off" can still evaluate how many files in their archive would benefit from enabling the patch.

---

## Feature Dependencies

| Feature | Requires | Scripts |
|---------|----------|---------|
| D50 Patch | exiftool | encoder |
| ICC Matrix | Pillow + ImageCms | decoder |
| Built-in Color Spaces | ImageMagick | transcoder |
| Interactive Wizard | rich (optional) | photo |
| JPEG Transcoding | cjxl/djxl 0.11+ | transcoder |
| PPM Validation | numpy | decoder |
| D50 Count in OFF Mode | exiftool | encoder |

---

## Backward Compatibility

All new features are **opt-in** and do not change default behavior:
- D50 Patch: Default `auto` (only applies when needed)
- Strip Mode: Default `False`
- Encode Tag: Default `xmp` (same as before)
- Sync Mode: Must explicitly use `--sync`
- Staging: Default `None` (disabled)
- Mode 8: Must explicitly use `--mode 8 --delete-source`
- Matrix Mode: Must explicitly use `--matrix`
