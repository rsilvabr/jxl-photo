# New Features Since v1.0

## v1.8.0

Date: 2026-07-18

### libjxl v0.12 Support with Automatic Version Detection

The scripts query `cjxl`/`djxl --version` once per process (cached) and only pass v0.12-only flags when the binary supports them. Older libjxl (or undetectable versions) behave exactly as before — no v0.12 flag is ever appended.

- **`djxl --reconstruct_jpeg` on lossless recovery** — transcode decode (JXL → JPEG) now asks djxl ≥ 0.12 for an authoritative lossless reconstruction that fails cleanly if impossible. Second guard alongside the `jbrd` box check; failures become per-file errors and the batch continues.
- **`--buffering` option (opt-in)** — new `CJXL_BUFFERING` setting in the TIFF encoder and JPEG transcoder, plus a `--buffering 0|1|2|3` CLI flag on the encoder. Default `None` (flag not passed; cjxl uses its fast default). `0` restores pre-0.12 maximum compression, but measured on real 45 MP lossless TIFFs it is only ~1.2% smaller for ~6× slower encodes — see the [v1.8.0 benchmark](RELEASE_v1.8.0.md).

---

## v1.5.1

Date: 2026-04-13

### Critical Bug Fix — 8-bit TIFF Conversion

**Location:** `jxl_tiff_encoder.py`

**The Bug:** When converting 8-bit TIFF files to JXL, images appeared completely black (~25 KB instead of ~25 MB). This was a critical data corruption bug affecting all 8-bit TIFF sources (NX Studio, GIMP, Lightroom 8-bit exports).

**Root Cause:** When converting 8→16 bit, pixel values were not scaled. Value 255 (white) became 255 in 0-65535 range = 0.39% brightness.

**Fix:** Proper scaling (multiply by 257 = 65535/255):
```python
if img.dtype == np.uint8:
    img = img.astype(np.uint16) * 257  # 0-255 → 0-65535
```

**Reported by:** WiseTomCat (NX Studio 8-bit LZW TIFFs)

---

## v1.5

Date: 2026-04-12

### Feature #8 — JXL→JPEG Auto Mode for Directories

**Location:** `jxl_jpeg_transcoder.py`, `jxl_photo.py`

**What changed:** The transcoder can now auto-detect per-file in batch mode:
- Files WITH jbrd box → lossless transcoding
- Files WITHOUT jbrd → lossy conversion

**CLI:**
```bash
python jxl_jpeg_transcoder.py folder/ --mode 8  # auto-detect per file
```

**Wizard:** Option [1] "JPEG Auto-Detect" now works for directories

---

### Feature #9 — Configurable JXL→TIFF Preview

**Location:** `jxl_tiff_decoder.py`

**What changed:** Users can now disable the embedded JPEG preview in output TIFF files.

**CLI:**
```bash
# With preview (default)
python jxl_tiff_decoder.py folder/ --mode 1

# Without preview (smaller files)
python jxl_tiff_decoder.py folder/ --mode 1 --no-preview
```

**Wizard:** Step 6 asks "Add JPEG preview?"

---

### Feature #10 — Complete Manifest System

**Location:** `jxl_photo.py`

**What changed:** Full workflow support via manifest CSV files:
- All flags supported (--staging, --embed-thumbnail, --delete-source, --no-preview, --encode-tag, --d50-patch)
- Consistent behavior between interactive and manifest modes
- Edit in Excel, comment lines with `#`

**Evolution from v1.3:** The manifest system in v1.3 was functional but missing many flags that existed in interactive mode. In v1.5, all missing flags were added, achieving full parity between interactive and manifest workflows. Previously, using a manifest would result in different behavior (missing thumbnails, different staging, etc.) — now both modes produce identical results.

---

### Feature #11 — D50 Patch Tracking in OFF Mode

**Location:** `jxl_tiff_encoder.py`

**What changed:** When D50 patch is disabled, the script now tracks statistics showing how many files were already correct vs would have needed patching.

---

## v1.4

Date: 2026-04-11

### Feature #7 — Embedded JPEG Thumbnail in JXL (Optional)

**Location:** `jxl_tiff_encoder.py`

**What changed:** Optional embedded JPEG thumbnail (256px, sRGB) in JXL files for fast preview in image viewers.

**CLI:**
```bash
python jxl_tiff_encoder.py folder/ --embed-thumbnail
```

---

### Improvements

- **Auto Mode improvements** — Better folder structure detection
- **Repeat workflow** — Now saves destination format correctly
- **Step 2 renumbering** — Fixed TIFF option numbering

---

## v1.3 (Legacy)

Date: 2026-04-11
Scripts: `jxl_photo.py` (formerly v2), `jxl_tiff_decoder.py`, `jxl_tiff_encoder.py`, `jxl_jpeg_transcoder.py`

---

### Feature #5 — Auto Mode + Manifest System (Beta)

**Location:** `jxl_photo.py`

> **Status:** Auto Mode is functional and works well for common folder structures, but is still being tested. For critical workflows, manual mode selection (0-8) remains the stable option.

**What changed:** Complete rebuild of the interactive wrapper with intelligent folder analysis.

**New Auto Mode:**
- Press `[A]` in Step 4 to analyze folder structure automatically
- Detects `_EXPORT`, `Export_*` folders (case-insensitive)
- Recommends best mode with confidence level (high/medium/low)
- Shows folder mapping preview (source → destination)

**New Manifest System:**
```
[A] Auto Mode → [P] Generate manifest → Edit in Excel → [M] Run from manifest
```
- Generate CSV manifest from folder analysis
- Edit paths, delete rows, reorder before running
- Comment out lines with `#` to skip temporarily
- Manifests saved in `manifests/` folder — rerun anytime
- Use with `--sync` to re-process only changed files

**Benefits:**
- No need to memorize modes 0-8
- Visual preview before execution
- Full control via Excel editing
- Safe workflow with manifest review

---

### Feature #6 — Capture One-Compatible TIFF Preview

**Location:** `jxl_tiff_decoder.py`

**What changed:** TIFF preview structure rebuilt to match Capture One behavior.

**Before (v1.0 - v1.2):**
- Page 0: Preview (1024px, ICC embedded)
- Page 1: Main 16-bit image
- Preview kept original color space (not sRGB)

**After (v1.3):**
- Page 0: Main 16-bit image (ICC embedded)
- Page 1: Preview (256px, sRGB, no ICC, thumbnail flag)
- Preview automatically converted to sRGB via LittleCMS

**Benefits:**
- Correct thumbnail colors in Windows Explorer
- Matches Capture One TIFF structure
- Smaller preview size (256px vs 1024px)
- ICC profile only on main image (standard behavior)

---

### Feature #7 — Embedded JPEG Thumbnail in JXL (Optional)

**Location:** `jxl_tiff_encoder.py`

**What changed:** Optional embedded JPEG thumbnail (256px, sRGB) in JXL files for fast preview in image viewers.

**How it works:**
- Generate 256px preview from source TIFF
- Convert to sRGB using LittleCMS (correct colors)
- Embed as EXIF ThumbnailImage via exiftool
- Adds ~15-30KB per file

**Enable:**
```python
# In jxl_tiff_encoder.py settings
EMBED_JPEG_THUMBNAIL = True
```

Or via CLI:
```bash
python jxl_tiff_encoder.py folder/ --embed-thumbnail
```

**Supported viewers:**
- ✅ IrfanView — shows thumbnail in file list (color fix reported, test with latest plugin)
- ✅ XnView MP — fast thumbnail preview
- ✅ digiKam — uses embedded thumbnail
- ✅ darktable — EXIF thumbnail support
- ❌ Windows Explorer — current WIC codec ignores embedded thumbnail and generates its own without color management

**Important — Windows Limitation:**
The Windows JXL WIC codec (from Microsoft Store) has two problems:
1. **Ignores the embedded EXIF thumbnail** — generates its own from scratch
2. **No color management** — converts ProPhoto/Adobe RGB to thumbnail without ICC profile, resulting in wrong/washed-out colors

This is a **Windows codec limitation**, not a bug in this software. The embedded thumbnail is correct (sRGB, properly converted), but Windows doesn't use it. For accurate thumbnails on Windows, use IrfanView, XnView MP, or digiKam.

**Note on EXIF in IrfanView:** While thumbnails display correctly with the latest plugin, EXIF visibility depends on JXL source:
- **TIFF → JXL:** EXIF visible ✅ (boxes reordered)
- **JPEG → JXL:** EXIF not visible ❌ (Brotli compression)

---

## v1.2

Date: 2026-04-05
Scripts: `jxl_tiff_decoder.py`, `jxl_photo.py`

---

### Feature #4 — Improved Basic Mode (ICC Preservation)

**Location:** `jxl_tiff_decoder.py`

**What changed:** The "Basic" decode mode now preserves the ICC profile generated by djxl, instead of discarding it entirely.

**Before (v1.0 - v1.1):**
- Basic mode decoded to PPM format (no ICC support)
- Output TIFF had no ICC profile attached
- Only useful for web/sRGB workflows

**After (v1.2):**
- Basic mode decodes to PNG format to capture ICC from djxl
- ICC profile generated by djxl is extracted and attached to output TIFF
- Makes more sense for most workflows where color accuracy matters

**New None Mode:**
The old "discard ICC" behavior is still available via `--none` flag (or `FORCE_NONE_MODE = True` in settings). Use this only if you specifically want no ICC profile.

**CLI Usage:**
```bash
# New Basic mode (preserves djxl ICC) — default when no XMP ICC
python jxl_tiff_decoder.py photo.jxl

# Force None mode (no ICC) — old behavior
python jxl_tiff_decoder.py photo.jxl --none

# All modes can be forced via flags
python jxl_tiff_decoder.py photo.jxl --basic   # Force Basic (djxl ICC)
python jxl_tiff_decoder.py photo.jxl --none    # Force None (no ICC)
python jxl_tiff_decoder.py photo.jxl --matrix  # Force Matrix (LittleCMS)
```

---

## v1.1

Date: 2026-04-04
Scripts: `jxl_photo.py`, `jxl_tiff_encoder.py`, `jxl_tiff_decoder.py`, `jxl_jpeg_transcoder.py`

---

### Summary Table

| # | Feature | Scripts | Note |
|---|---------|---------|------|
| 1 | D50 Illuminant Patch (with modes) | encoder | v1.0 had basic patch; modes (auto/on/off) are new |
| 2 | Metadata Strip Mode | encoder | Did not exist in v1.0 |
| 3 | D50 Count in OFF Mode | encoder | New tracking when patch is disabled |

**All bug fixes from v1.0 are documented in `bug_tracking_since_v1.0.md`.**

---

### Feature #1 — D50 Illuminant Patch (with modes)

**Location:** `jxl_tiff_encoder.py`

**What changed since v1.0:** In v1.0, the D50 patch was always applied unconditionally to all files.

**New behavior:**
- `auto` (default): Detects Capture One exports via EXIF Software field and applies patch only when needed
- `on`: Always apply D50 patch
- `off`: Never apply D50 patch (but tracks correctness — see Feature #3)

**CLI Usage:**
```bash
python jxl_tiff_encoder.py folder/ --d50-patch auto
python jxl_tiff_encoder.py folder/ --d50-patch on
python jxl_tiff_encoder.py folder/ --d50-patch off
```

**Wizard:** Step 6 (Basic Parameters) asks for D50 patch mode when TIFF→JXL.

**Bug fixed:** D50 patch was unconditional in v1.0 — now respects modes and D50_PATCH_SOFTWARE_LIST.

---

### Feature #2 — Metadata Strip Mode

**Location:** `jxl_tiff_encoder.py`

**What changed since v1.0:** This feature did NOT exist in v1.0.

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

---

### Feature #3 — D50 Count in OFF Mode

**Location:** `jxl_tiff_encoder.py`

**What changed since v1.0:** When D50_PATCH_MODE="off", the script now tracks correctness even though no patching is applied.

**Description:** Users can see how many files were already correct vs would have needed patching, helping them decide if they should enable patching.

**Summary Output:**
```
# mode: off — shows what would have happened
D50 patch: 2 already correct | 8 would have needed (mode: off)
```

---

## Bug Fixes Summary

**All bugs from v1.0 and v1.1 are documented in `bug_tracking_since_v1.0.md`.**

**Code quality and compatibility notes are in `code_quality_refactoring.md`.**

Key fixes that improved robustness:
- Race conditions in staging directory (UUID added)
- Integer overflow in JXL box parser
- PPM truncation detection
- Deadlock in djxl+ImageMagick pipeline
- Distance parameter passed to cjxl correctly
- exiftool warning filtering in metadata
- lossless_jpeg=1 incompatible with distance>0
